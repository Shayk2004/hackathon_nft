import requests

def get_osm_administrative_areas(lat, lon):
    """
    Queries OpenStreetMap (Overpass API) to retrieve all administrative areas
    that contain the given latitude and longitude and have a Wikidata tag.
    
    Args:
        lat (float): Latitude of the location.
        lon (float): Longitude of the location.
    
    Returns:
        list: A list of dictionaries containing administrative area name, Wikidata ID,
              and admin_level, sorted in descending order by admin_level (smallest area first).
    """
    overpass_url = "http://overpass-api.de/api/interpreter"
    # The query returns all administrative areas with a Wikidata tag.
    overpass_query = f"""
    [out:json];
    is_in({lat},{lon})->.a;
    area.a["boundary"="administrative"]["wikidata"]["name"];
    out body;
    """
    try:
        response = requests.get(overpass_url, params={"data": overpass_query})
        response.raise_for_status()
        data = response.json()
        areas = []
        if "elements" in data and len(data["elements"]) > 0:
            for element in data["elements"]:
                if "tags" in element and "wikidata" in element["tags"]:
                    try:
                        admin_level = int(element["tags"].get("admin_level", 99))
                    except ValueError:
                        admin_level = 99
                    areas.append({
                        "name": element["tags"].get("name"),
                        "wikidata_id": element["tags"].get("wikidata"),
                        "admin_level": admin_level
                    })
            # Sort by admin_level in descending order: higher admin_level means a smaller area.
            sorted_areas = sorted(areas, key=lambda x: x["admin_level"], reverse=True)
            return sorted_areas
        return []
    except requests.exceptions.RequestException as e:
        print("Request error:", e)
        return []

def get_population_and_area_wikidata(wikidata_id):
    """
    Queries Wikidata to fetch the population and area of an administrative region given its Wikidata ID.
    Uses a UNION query to try retrieving the population either via the truthy property (wdt:P1082)
    or from the full statement (p:P1082) if the truthy value isn’t available.
    
    Args:
        wikidata_id (str): The Wikidata ID of the administrative region.
    
    Returns:
        dict: Population, area, and calculated population density if available;
              otherwise an error message.
    """
    wikidata_url = "https://query.wikidata.org/sparql"
    sparql_query = f"""
    SELECT ?population ?area WHERE {{
      {{
        wd:{wikidata_id} wdt:P1082 ?population .
      }}
      UNION
      {{
        wd:{wikidata_id} p:P1082 ?popStatement .
        ?popStatement ps:P1082 ?population .
      }}
      OPTIONAL {{ wd:{wikidata_id} wdt:P2046 ?area . }}
    }} LIMIT 1
    """
    headers = {"Accept": "application/json"}

    try:
        response = requests.get(wikidata_url, params={"query": sparql_query, "format": "json"}, headers=headers)
        response.raise_for_status()
        data = response.json()

        if ("results" in data and "bindings" in data["results"] and 
            len(data["results"]["bindings"]) > 0):
            result = data["results"]["bindings"][0]
            try:
                population = int(result["population"]["value"])
            except (KeyError, ValueError):
                return {"error": "Population data is not in a valid format."}
            area = None
            if "area" in result:
                try:
                    area = float(result["area"]["value"])
                except ValueError:
                    area = None

            if area:
                density = population / area  # Compute population density
                return {
                    "population": population,
                    "area_km2": area,
                    "population_density": density
                }
            else:
                return {
                    "population": population,
                    "area_km2": "Unknown",
                    "population_density": "Cannot calculate (no area data)"
                }
        return {"error": "No population or area data found for this Wikidata ID."}
    
    except requests.exceptions.RequestException as e:
        return {"error": f"Request failed: {str(e)}"}

def search_alternative_wikidata_ids(area_name):
    """
    Searches Wikidata for alternative entities with the given name.
    
    Args:
        area_name (str): The name of the administrative area.
    
    Returns:
        list: A list of candidate Wikidata IDs.
    """
    search_url = "https://www.wikidata.org/w/api.php"
    params = {
        "action": "wbsearchentities",
        "search": area_name,
        "language": "en",
        "format": "json"
    }
    try:
        response = requests.get(search_url, params=params)
        response.raise_for_status()
        data = response.json()
        results = data.get("search", [])
        # Return candidate IDs, excluding duplicates (and possibly the one we already tried)
        return [r["id"] for r in results]
    except requests.exceptions.RequestException as e:
        print("Error searching Wikidata:", e)
        return []

def get_population_density(lat, lon):
    """
    Combines OSM and Wikidata methods to find the population density of a given latitude/longitude.
    If the smallest administrative area does not have valid population data, this function
    automatically checks progressively larger areas until valid data is found.
    If an area’s Wikidata ID does not return valid data, it will search for alternative Wikidata IDs
    using the area name.
    
    Args:
        lat (float): Latitude of the location.
        lon (float): Longitude of the location.
    
    Returns:
        dict: Contains the name, admin_level, population, area, and density of the first administrative region
              that has valid data, or an error message if none is found.
    """
    areas = get_osm_administrative_areas(lat, lon)
    if not areas:
        return {"error": "No administrative areas found for this location."}
    
    # Loop over areas starting with the smallest (highest admin_level) and move to larger areas.
    for area in areas:
        wikidata_id = area["wikidata_id"]
        print(f"Trying area '{area['name']}' with Wikidata ID: {wikidata_id}")
        population_data = get_population_and_area_wikidata(wikidata_id)
        if "error" not in population_data:
            return {
                "location": area["name"],
                "admin_level": area["admin_level"],
                "population": population_data["population"],
                "area_km2": population_data["area_km2"],
                "population_density": population_data["population_density"]
            }
        else:
            print(f"Area '{area['name']}' (Wikidata ID: {wikidata_id}) did not return valid data.")
            # Try searching for alternative Wikidata IDs using the area name.
            alternatives = search_alternative_wikidata_ids(area["name"])
            for alt_id in alternatives:
                # Skip if it's the same as the one we already tried.
                if alt_id == wikidata_id:
                    continue
                print(f"Trying alternative Wikidata ID: {alt_id} for area '{area['name']}'")
                alt_population_data = get_population_and_area_wikidata(alt_id)
                if "error" not in alt_population_data:
                    return {
                        "location": area["name"],
                        "admin_level": area["admin_level"],
                        "population": alt_population_data["population"],
                        "area_km2": alt_population_data["area_km2"],
                        "population_density": alt_population_data["population_density"],
                        "wikidata_id": alt_id  # indicate alternative ID used
                    }
            print(f"No alternative Wikidata IDs for '{area['name']}' returned valid data. Trying a larger area...")
    
    return {"error": "Could not fetch population or area data from Wikidata for any administrative area."}

# Example Usage
if __name__ == "__main__":
    # Example coordinates for New Delhi, India.
    latitude = 27.7785
    longitude = 87.9482

    final_population_density = get_population_density(latitude, longitude)
    print("Population Density Data:", final_population_density)
