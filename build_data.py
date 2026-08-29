"""Precompute compact drive-pie data for every game in a season's nflverse play-by-play."""
import json, sys, math, re
import pandas as pd

SCRIMMAGE = {"pass", "run", "qb_kneel", "qb_spike"}
ENDERS = {"punt", "field_goal"}

def clean_desc(desc):
    desc = str(desc) if isinstance(desc, str) else ""
    desc = re.sub(r"^\(\d+:\d+\)\s*", "", desc)
    desc = re.sub(r"\((Shotgun|No Huddle|No Huddle, Shotgun)\)\s*", "", desc)
    desc = re.sub(r"\b\d+-([A-Z]\.)", r"\1", desc)
    return desc[:110]

def clock_to_elapsed(qtr, clock):
    m, s = clock.split(":")
    return (qtr - 1) * 900 + (900 - (int(m) * 60 + int(s)))

def ot_elapsed(qtr, clock, ot_len):
    m, s = clock.split(":")
    return 3600 + (qtr - 5) * ot_len + (ot_len - (int(m) * 60 + int(s)))

def play_elapsed(p, ot_len):
    if int(p.qtr) <= 4:
        return int(3600 - p.game_seconds_remaining)
    qsr = p.quarter_seconds_remaining if pd.notna(p.quarter_seconds_remaining) else 0
    return int(3600 + (int(p.qtr) - 5) * ot_len + (ot_len - qsr))

def drive_start(d, ot_len):
    rows = d.dropna(subset=["game_seconds_remaining", "qtr"])
    if rows.empty: return None
    return play_elapsed(rows.iloc[0], ot_len)

def game_data(g):
    g = g.reset_index(drop=True)
    home, away = g.home_team.iloc[0], g.away_team.iloc[0]
    qs = []
    for q in (1, 2, 3, 4):
        sub = g[g.qtr == q]
        if sub.empty:
            qs.append(qs[-1] if qs else [0, 0]); continue
        last = sub.iloc[-1]
        qs.append([int(last.total_away_score), int(last.total_home_score)])
    ot = bool(g.qtr.max() > 4)
    ot_len = 900 if g.season_type.iloc[0] != "REG" else 600
    groups = [(did, d) for did, d in g.groupby("fixed_drive", sort=True)]
    drives = []
    for i, (did, d) in enumerate(groups):
        scrim = d[d.play_type.isin(SCRIMMAGE | ENDERS)]
        teams = (scrim.posteam.dropna() if not scrim.posteam.dropna().empty else d.posteam.dropna())
        if teams.empty: continue
        team = teams.mode().iloc[0]
        opp = away if team == home else home
        plays = d[d.play_type.isin(SCRIMMAGE | ENDERS)]
        if plays.empty: continue
        a0 = drive_start(d, ot_len)
        if a0 is None: continue
        nxt = None
        for j in range(i + 1, len(groups)):
            nxt = drive_start(groups[j][1], ot_len)
            if nxt is not None: break
        end_of_period = 3600 if a0 < 3600 else 3600 + math.ceil((a0 - 3599) / ot_len) * ot_len
        a1 = nxt if nxt is not None else end_of_period
        if a0 < 1800 <= a1: a1 = 1800                            # a drive never crosses halftime
        a1 = max(a1, a0 + 1)
        half = 1 if a0 < 1800 else (2 if a0 < 3600 else 3)
        upto = groups[i + 1][1].index[0] if i + 1 < len(groups) else g.index[-1] + 1
        window = g.loc[: upto - 1]
        score = [int(window.total_away_score.max()), int(window.total_home_score.max())]

        seq = []
        for _, p in d.iterrows():
            if p.play_type not in SCRIMMAGE | ENDERS | {"no_play"}: continue
            if pd.isna(p.yardline_100) or pd.isna(p.game_seconds_remaining): continue
            is_pen = p.penalty == 1 and isinstance(p.penalty_team, str)
            if p.play_type == "no_play" and not is_pen: continue
            e = play_elapsed(p, ot_len)
            frac = round((100 - p.yardline_100) / 100, 3)
            if is_pen: kind = "o" if p.penalty_team == team else "d"
            elif p.play_type in SCRIMMAGE: kind = "f" if p.first_down == 1 else "p"
            else: kind = "e"
            desc = clean_desc(p.desc)
            down = int(p.down) if pd.notna(p.down) else 0
            ydstogo = int(p.ydstogo) if pd.notna(p.ydstogo) else 0
            seq.append([e, frac, kind, down, ydstogo, desc])
        seq.sort(key=lambda r: r[0])
        if not seq: continue

        last = plays.iloc[-1]
        result = d.fixed_drive_result.iloc[0]
        end_frac = (100 - last.yardline_100 + (last.yards_gained if last.play_type in SCRIMMAGE and pd.notna(last.yards_gained) else 0)) / 100
        tov, word, sb, pr = None, "", None, None
        xtra = None
        if result == "Touchdown":
            end_frac, word, sb = 1.0, "TD", team
            two = d[d.two_point_attempt == 1]
            xp = d[d.extra_point_attempt == 1]
            if not two.empty:
                row = two.iloc[-1]
                xtra = dict(k="2PT", ok=bool(row.two_point_conv_result == "success"), d=clean_desc(row.desc))
            elif not xp.empty:
                row = xp.iloc[-1]
                xtra = dict(k="XP", ok=bool(row.extra_point_result == "good"), d=clean_desc(row.desc))
        elif result == "Field goal": word, sb = "FG", team
        elif result == "Punt":
            word = "Punt"
            if last.play_type == "punt" and pd.notna(last.kick_distance):
                land = last.yardline_100 - last.kick_distance                    # where it came down, still in the punting team's frame
                ret = last.return_yards if pd.notna(last.return_yards) else 0
                if last.touchback == 1: land, ret = 20, 0
                f_land = max(0.0, min(1.0, (100 - land) / 100))
                f_after = max(0.0, min(1.0, (100 - (land + ret)) / 100))
                # the drive line stops at the snap; the punt's flight is not a drive event,
                # only the return is drawn, and only in the returning team's color
                pr = dict(fl=round(f_land, 3), fa=round(f_after, 3), d=clean_desc(last.desc))
        elif result == "Missed field goal": word = "FG miss"
        elif result == "End of half": word = "Half" if half == 1 else "Final"
        elif result == "Safety": end_frac, word, sb = 0.0, "Safety", opp
        elif result in ("Turnover", "Opp touchdown", "Turnover on downs"):
            if last.interception == 1 or last.fumble_lost == 1:
                word = "INT" if last.interception == 1 else "Fumble"
                air = last.air_yards if (last.interception == 1 and pd.notna(last.air_yards)) else 0
                f_int = (100 - (last.yardline_100 - air)) / 100
                ret = last.return_yards if pd.notna(last.return_yards) else 0
                ret_td = bool(last.return_touchdown == 1)
                f_after = 0.0 if ret_td else max(0.0, min(1.0, f_int - ret / 100))
                end_frac = f_int
                tov = dict(fi=round(f_int, 3), fa=round(f_after, 3), td=ret_td)
                if ret_td: word, sb = ("Pick 6" if last.interception == 1 else "Fumble 6"), opp
            else: word = "Downs"
        else: word = result if isinstance(result, str) else ""
        end_frac = max(0.0, min(1.0, end_frac))
        seq.append([a1, round(end_frac, 3), "x", 0, 0, ""])
        n = sum(1 for r in seq if r[2] in ("p", "f"))
        drives.append(dict(t=team, h=half, a0=a0, a1=a1, w=word, sb=sb, sc=score, tov=tov, xt=xtra, pr=pr,
                           q=seq, n=n, y=int(round((end_frac - seq[0][1]) * 100)), r=result))
    for a, b in zip(drives, drives[1:]):                        # close any sliver left by a dropped micro-drive
        if 0 < b["a0"] - a["a1"] <= 90 and not (a["a1"] <= 1800 < b["a0"]): a["a1"] = b["a0"]
    if not drives: return None
    k1 = away if drives[0]["t"] == home else home
    h2 = [dr for dr in drives if dr["h"] == 2]
    k2 = (away if h2[0]["t"] == home else home) if h2 else k1
    top = {}
    for t in (home, away):
        mine = [dr for dr in drives if dr["t"] == t]
        top[t] = dict(s=sum(dr["a1"] - dr["a0"] for dr in mine), n=len(mine))
    last = g.iloc[-1]
    stad = g.stadium.dropna()
    return dict(id=g.game_id.iloc[0], top=top, stad=(stad.iloc[0] if not stad.empty else None), wk=int(g.week.iloc[0]), st=g.season_type.iloc[0],
                home=home, away=away, date=str(g.game_date.iloc[0]),
                hs=int(last.total_home_score), as_=int(last.total_away_score),
                qs=qs, ot=ot, otlen=ot_len, k1=k1, k2=k2, drives=drives)

def main(pbp_path, teams_path, out_path):
    pbp = pd.read_csv(pbp_path, low_memory=False)
    games = []
    for gid, g in pbp.groupby("game_id", sort=True):
        gd = game_data(g)
        if gd: games.append(gd)
    games.sort(key=lambda x: (x["st"] != "REG", x["wk"], x["date"], x["id"]))
    teams = pd.read_csv(teams_path)
    tc = {r.team_abbr: dict(name=r.team_name, nick=r.team_nick,
                            colors=[c for c in [r.team_color, r.team_color2, r.team_color3, r.team_color4] if isinstance(c, str)])
          for r in teams.itertuples()}
    json.dump(dict(games=games, teams=tc), open(out_path, "w"), separators=(",", ":"))
    print(len(games), "games")

if __name__ == "__main__":
    main(*sys.argv[1:4])
