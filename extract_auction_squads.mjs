// extract_auction_squads.mjs — dump the cricket-auction-helper league/tour squad SEEDS as JSON
// so tour_sync.py can use them as the squad source for leagues (cricapi carries no franchise
// squads). Emits to stdout:
//   [{ file, export, gender, teams: [{ name, short, players: [{name, role}] }] }]
//
// Usage:  node extract_auction_squads.mjs /path/to/cricket-auction-helper > auction_squads.json
// Needs Node >= 22 (TypeScript type-stripping). Each seed is imported independently; a seed
// that fails to import is skipped with a warning rather than aborting the whole run.
import { readdirSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const repo = process.argv[2] || ".";
const dir = join(repo, "src/lib/squads");

let files = [];
try {
  files = readdirSync(dir).filter((f) => /-20\d\d.*\.ts$/.test(f)); // dated seed files
} catch (e) {
  console.error(`cannot read ${dir}: ${e.message}`);
  process.exit(1);
}

const out = [];
for (const f of files) {
  let mod;
  try {
    mod = await import(pathToFileURL(join(dir, f)).href);
  } catch (e) {
    console.error(`skip ${f}: ${e.message}`);
    continue;
  }
  for (const [exp, val] of Object.entries(mod)) {
    // a "team array" = a non-empty array whose elements have a name + a players array
    if (!Array.isArray(val) || val.length === 0) continue;
    const first = val[0];
    if (!first || typeof first !== "object" || typeof first.name !== "string" || !Array.isArray(first.players))
      continue;
    const gender = /WOMEN/i.test(exp) ? "female" : /MEN/i.test(exp) ? "male" : "unknown";
    const teams = val
      .filter((t) => t && typeof t.name === "string" && Array.isArray(t.players))
      .map((t) => ({
        name: t.name,
        short: t.short || t.code || t.name.slice(0, 4).toUpperCase(),
        players: t.players
          .filter((p) => p && p.name)
          .map((p) => ({ name: p.name, role: p.role || "BAT" })),
      }));
    if (teams.length) out.push({ file: f, export: exp, gender, teams });
  }
}
process.stdout.write(JSON.stringify(out));
console.error(`extracted ${out.length} seed export(s): ${out.map((s) => `${s.export}(${s.teams.length})`).join(", ")}`);
