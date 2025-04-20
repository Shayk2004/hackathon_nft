// createNFT.js
const { Connection, clusterApiUrl, Keypair } = require('@solana/web3.js');
// import { Connection, clusterApiUrl, Keypair } from '@solana/web3.js';
const { Metaplex, keypairIdentity, irysStorage, toMetaplexFile } = require('@metaplex-foundation/js');
// import { Metaplex, keypairIdentity, irysStorage, toMetaplexFile } from '@metaplex-foundation/js';
const fs = require('fs');
// import fs from 'fs';
const path = require('path');
// import path from 'path';

async function createNFT(imagePath, name, symbol, description) {
  // 1. Set up a connection to the Solana Devnet.
  const connection = new Connection(clusterApiUrl('devnet'));
  const keypairPath = path.join(process.env.HOME, ".config", "solana", "id.json");
  const secretKey = JSON.parse(fs.readFileSync(keypairPath));
  const wallet = Keypair.fromSecretKey(new Uint8Array(secretKey));

  // 2. Define your RPC endpoint.
  const QUICKNODE_RPC = 'https://api.devnet.solana.com'; // Replace with your RPC URL if needed

  // 3. Initialize Metaplex with your wallet and configure Irys storage.
  const metaplex = Metaplex.make(connection)
    .use(keypairIdentity(wallet))
    .use(irysStorage({
      address: 'https://devnet.irys.xyz',
      providerUrl: QUICKNODE_RPC,
      timeout: 120000,
    }));

  // 4. Read the image file from disk and convert it to a Metaplex file.
  const imageBuffer = fs.readFileSync(imagePath);
  const file = toMetaplexFile(imageBuffer, 'image.png');

  // 5. Upload the image to decentralized storage (via Irys).
  const imageUri = await metaplex.storage().upload(file);
  console.log('Image uploaded to:', imageUri);

  // 6. Mint the NFT with the uploaded image URI.
  const { nft } = await metaplex.nfts().create({
    uri: imageUri,
    name: name,
    sellerFeeBasisPoints: 500, // e.g., 5% royalties
    symbol: symbol,
    creators: [
      {
        address: wallet.publicKey,
        verified: true,
        share: 100,
      },
    ],
  });

  console.log('NFT created with address:', nft.address.toBase58());
}

// Call the function with your image file path and metadata
createNFT('image.png', 'My NFT Name', 'MYNFT', 'This is a description of my NFT.')
  .catch(err => {
    console.error(err);
  });
