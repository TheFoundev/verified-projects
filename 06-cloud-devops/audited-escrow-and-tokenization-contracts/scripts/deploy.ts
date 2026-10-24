import { ethers } from "hardhat";

/**
 * Deploys the TokenizedAsset collection and the EscrowFactory, then prints
 * their addresses.
 *
 * Run against the in-process Hardhat network:
 *   npm run deploy
 * or against a local node:
 *   npx hardhat node            (terminal 1)
 *   npm run deploy -- --network localhost   (terminal 2)
 */
async function main() {
  const [deployer] = await ethers.getSigners();
  console.log(`Deployer: ${deployer.address}`);

  // address(0) => no companion royalty token in the default deployment.
  // Deploy an ERC20 first and pass its address to enable royalty pooling.
  const token = await ethers.deployContract("TokenizedAsset", [
    "Foundev Tokenized Asset",
    "FTA",
    ethers.ZeroAddress,
  ]);
  await token.waitForDeployment();

  const factory = await ethers.deployContract("EscrowFactory");
  await factory.waitForDeployment();

  console.log(`TokenizedAsset: ${await token.getAddress()}`);
  console.log(`EscrowFactory : ${await factory.getAddress()}`);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
