"""Assemble the Drive Wheel production site as static files: a small shell (nfl_template.html),
one manifest for the pickers, one file per game (fetched only when that game is opened), the
team color table, and the logo sprite. Nothing is embedded, so the shell stays a few dozen KB
regardless of how many seasons of games are behind it.

Usage: python build_nfl.py [outdir]     (default: site)
Reads every data*.json in this folder (one per season, from build_data.py).
"""
import glob, json, os, sys

MANIFEST_FIELDS = ("id", "wk", "st", "date", "home", "away", "hs", "as_", "ot")

def main(outdir="site"):
    games_dir = os.path.join(outdir, "games")
    os.makedirs(games_dir, exist_ok=True)

    manifest, teams = [], {}
    for path in sorted(glob.glob("data*.json")):
        season = json.load(open(path))
        teams.update(season["teams"])
        for g in season["games"]:
            g["season"] = g["id"][:4]
            manifest.append({k: g[k] for k in MANIFEST_FIELDS} | {"season": g["season"]})
            with open(os.path.join(games_dir, g["id"] + ".json"), "w") as f:
                json.dump(g, f, separators=(",", ":"))

    manifest.sort(key=lambda g: (g["season"], g["st"] != "REG", g["wk"], g["date"], g["id"]))
    json.dump(manifest, open(os.path.join(games_dir, "manifest.json"), "w"), separators=(",", ":"))
    json.dump(teams, open(os.path.join(outdir, "teams.json"), "w"), separators=(",", ":"))

    with open(os.path.join(outdir, "index.html"), "w") as f:
        f.write(open("nfl_template.html").read())

    shell_kb = os.path.getsize(os.path.join(outdir, "index.html")) / 1024
    manifest_kb = os.path.getsize(os.path.join(games_dir, "manifest.json")) / 1024
    avg_game_kb = sum(os.path.getsize(os.path.join(games_dir, g["id"] + ".json")) for g in manifest) / len(manifest) / 1024
    print(f"{outdir}/: {len(manifest)} games")
    print(f"  index.html {shell_kb:.0f} KB · manifest.json {manifest_kb:.0f} KB · avg game.json {avg_game_kb:.1f} KB")
    print(f"  a visit loads the shell, the manifest and one game: ~{shell_kb + manifest_kb + avg_game_kb:.0f} KB uncompressed")

if __name__ == "__main__":
    main(*sys.argv[1:2])
