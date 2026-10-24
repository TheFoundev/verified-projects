import { HardhatUserConfig } from "hardhat/config";
import "@nomicfoundation/hardhat-toolbox";

/**
 * Network endpoints and the deployer key are read from the environment.
 * See `.env.example` for the expected variable names.
 *
 * PRIVATE_KEY must NEVER be a real key in a committed file — export it in
 * your shell (or source an untracked .env) before running a live deploy.
 */
const localhostUrl = process.env.LOCALHOST_RPC_URL ?? "http://127.0.0.1:8545";
const sepoliaUrl = process.env.SEPOLIA_RPC_URL;
const accounts = process.env.PRIVATE_KEY ? [process.env.PRIVATE_KEY] : [];

const config: HardhatUserConfig = {
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: { enabled: true, runs: 200 },
      // OpenZeppelin 5.6 uses `mcopy` (Cancun opcode) in utils/Bytes.sol.
      evmVersion: "cancun",
    },
  },
  networks: {
    hardhat: {},
    localhost: { url: localhostUrl, accounts },
    ...(sepoliaUrl ? { sepolia: { url: sepoliaUrl, accounts } } : {}),
  },
  typechain: {
    outDir: "typechain-types",
    target: "ethers-v6",
  },
  mocha: {
    timeout: 120_000,
  },
};

export default config;
