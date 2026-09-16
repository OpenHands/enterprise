import { readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const root = join(process.cwd(), "src");
const boundaries = new Set([
  "api/auth-adapter.ts",
  "api/option-service/option-service.api.ts",
  "api/option-service/option.types.ts",
  "hooks/use-authentication.ts",
]);

function modeReads(source: string): number[] {
  const tree = ts.createSourceFile("source.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const results: number[] = [];
  const inspect = (node: ts.Node): void => {
    if (
      (ts.isPropertyAccessExpression(node) && node.name.text === "auth_mode") ||
      (ts.isElementAccessExpression(node) && ts.isStringLiteral(node.argumentExpression) && node.argumentExpression.text === "auth_mode") ||
      (ts.isBindingElement(node) && (node.propertyName?.getText(tree) || node.name.getText(tree)) === "auth_mode")
    ) results.push(tree.getLineAndCharacterOfPosition(node.getStart(tree)).line + 1);
    ts.forEachChild(node, inspect);
  };
  inspect(tree);
  return results;
}

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

describe("authentication composition boundary", () => {
  it("keeps mode selection out of components and application hooks", () => {
    const violations = sourceFiles(root).flatMap((path) => {
      const name = relative(root, path).split(sep).join("/");
      if (boundaries.has(name)) return [];
      return modeReads(readFileSync(path, "utf8")).map((line) => `${name}:${line}`);
    });
    expect(violations).toEqual([]);
  });

  it("detects direct access and destructuring aliases", () => {
    expect(modeReads('const { auth_mode: local } = config; if (local === "native") run();')).toEqual([1]);
    expect(modeReads('const mode = config["auth_mode"];')).toEqual([1]);
  });
});
