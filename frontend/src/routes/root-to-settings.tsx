import { redirect } from "react-router";

/**
 * Enterprise settings is the primary surface in this app shell.
 * Send `/` straight into the settings tree.
 */
export function clientLoader() {
  return redirect("/settings");
}

export default function RootToSettings() {
  return null;
}
