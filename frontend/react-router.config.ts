import { promises as fs } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { Config } from "@react-router/dev/config";

/**
 * This script is used to unpack the client directory from the frontend build directory.
 * Remix SPA mode builds the client directory into the build directory. This function
 * moves the contents of the client directory to the build directory and then removes the
 * client directory.
 *
 * This script is used in the buildEnd function of the Vite config.
 */
const unpackClientDirectory = async () => {
  const buildDir = fileURLToPath(new URL("./build", import.meta.url));
  const clientDir = path.resolve(buildDir, "client");

  const files = await fs.readdir(clientDir);
  await Promise.all(
    files.map((file) =>
      fs.rename(path.resolve(clientDir, file), path.resolve(buildDir, file)),
    ),
  );

  await fs.rmdir(clientDir);
};

export default {
  appDirectory: "src",
  buildEnd: unpackClientDirectory,
  ssr: false,
} satisfies Config;
