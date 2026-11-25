#!/usr/bin/env python3
"""Generate Solar Ember themed SVG profile cards and sync README telemetry."""

from __future__ import annotations

import datetime as dt
import html
import io
import json
import math
import os
import re
import subprocess
import textwrap
import urllib.request
from pathlib import Path
from typing import Any

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "assets"
README_PATH = ROOT / "README.md"

DEFAULT_USERNAME = "drexjgobgt"
SVG_WIDTH = 840

# Solar Ember & Gold palette quantized from drexjgobgt's avatar
PALETTE = {
    "bg_outer": "#0D0605",
    "bg_card": "#160A08",
    "bg_avatar": "#1B0908",
    "tile": "#210E0B",
    "tile_elev": "#2E1410",
    "border": "#4B231D",
    "border_active": "#6C312B",
    "border_glow": "#A13E24",
    "ember": "#C65428",
    "orange": "#EB7827",
    "gold": "#EDA330",
    "solar": "#F8E543",
    "blaze": "#FDFE75",
    "text_primary": "#FFF7ED",
    "text_secondary": "#E7C8B5",
    "text_muted": "#A67C6B",
}

SPECTRUM_COLORS = [
    PALETTE["solar"],
    PALETTE["orange"],
    PALETTE["gold"],
    PALETTE["ember"],
    "#E88B5A",
    PALETTE["border_glow"],
]

CURATED_DESCRIPTIONS = {
    "KAGE": "Lightweight instant replay & direct MP4 capture studio for Windows.",
    "NusaPulse": "Indonesian food prices, weather, and anomaly analytics console.",
    "shortsgenerator": "Automated local Python pipeline for generating YouTube Shorts.",
    "mini_crm": "Web-based daily CRM built for Indonesian UMKM businesses.",
    "simple-e-commerce-umkm": "E-commerce platform for Indonesian UMKM, deployed on Vercel.",
    "SPK_TOPSIS": "TOPSIS decision-support dashboard for boarding house selection.",
}

FEATURED_ORDER = ["KAGE", "NusaPulse", "shortsgenerator", "mini_crm"]

GRAPHQL_QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    name
    avatarUrl(size: 460)
    bio
    followers { totalCount }
    following { totalCount }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC) {
      totalCount
      nodes {
        name
        description
        stargazerCount
        forkCount
        url
        homepageUrl
        updatedAt
        primaryLanguage { name color }
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges {
            size
            node { name color }
          }
        }
      }
    }
    contributionsCollection {
      totalCommitContributions
      totalIssueContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""


def esc(value: Any) -> str:
    """XML-escape text for safe embedding inside SVG elements."""
    return html.escape(str(value), quote=True)


def get_github_token() -> str:
    """Resolve GitHub token from env or gh CLI."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    try:
        res = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            check=True,
        )
        token = res.stdout.strip()
        if token:
            return token
    except Exception as exc:
        raise RuntimeError(
            "GITHUB_TOKEN not set and 'gh auth token' failed."
        ) from exc
    raise RuntimeError("Empty GitHub token.")


def fetch_github_data(username: str, token: str) -> dict[str, Any]:
    """Query GitHub GraphQL API for user profile, repos, and contributions."""
    payload = json.dumps(
        {"query": GRAPHQL_QUERY, "variables": {"login": username}}
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "drexjgobgt-profile-generator",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if "errors" in body:
        raise RuntimeError(f"GraphQL error: {body['errors']}")
    user = body.get("data", {}).get("user")
    if not user:
        raise RuntimeError(f"User '{username}' not found in GraphQL response.")
    return user


def fetch_avatar_bytes(avatar_url: str) -> bytes:
    """Download the user's GitHub avatar image."""
    req = urllib.request.Request(
        avatar_url,
        headers={"User-Agent": "drexjgobgt-profile-generator"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def compute_streaks(days: list[dict[str, Any]]) -> tuple[int, int]:
    """Compute (longest_streak, current_streak) from chronological calendar days."""
    if not days:
        return 0, 0
    sorted_days = sorted(days, key=lambda d: d["date"])
    longest = 0
    run = 0
    for d in sorted_days:
        if d["contributionCount"] > 0:
            run += 1
            if run > longest:
                longest = run
        else:
            run = 0

    # Current streak: if today has 0 commits so far, allow ending yesterday
    idx = len(sorted_days) - 1
    if idx > 0 and sorted_days[idx]["contributionCount"] == 0:
        idx -= 1
    current = 0
    while idx >= 0 and sorted_days[idx]["contributionCount"] > 0:
        current += 1
        idx -= 1
    return longest, current


def relative_time(iso_ts: str) -> str:
    """Format an ISO-8601 timestamp into a compact relative age string."""
    now = dt.datetime.now(dt.timezone.utc)
    parsed = dt.datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    delta = max(0, int((now - parsed).total_seconds()))
    days = delta // 86400
    if days == 0:
        hours = max(1, delta // 3600)
        return f"{hours}h ago"
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


def analyze_profile(user: dict[str, Any]) -> dict[str, Any]:
    """Derive summary stats, language weights, and featured repositories."""
    repos = user["repositories"]["nodes"]
    own_repos = len(repos)
    total_stars = sum(r["stargazerCount"] for r in repos)
    followers = user["followers"]["totalCount"]

    cal = user["contributionsCollection"]["contributionCalendar"]
    weeks = cal["weeks"]
    total_contributions = cal["totalContributions"]

    flat_days: list[dict[str, Any]] = []
    for week in weeks:
        flat_days.extend(week["contributionDays"])

    active_days = sum(1 for d in flat_days if d["contributionCount"] > 0)
    busiest = max(
        flat_days,
        key=lambda d: d["contributionCount"],
        default={"contributionCount": 0, "date": ""},
    )
    longest_streak, current_streak = compute_streaks(flat_days)

    # Aggregate language bytes across owned public non-fork repos
    lang_bytes: dict[str, int] = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            lname = edge["node"]["name"]
            lang_bytes[lname] = lang_bytes.get(lname, 0) + edge["size"]

    total_lang_bytes = sum(lang_bytes.values()) or 1
    sorted_langs = [
        {
            "name": name,
            "bytes": size,
            "pct": (size / total_lang_bytes) * 100.0,
        }
        for name, size in sorted(lang_bytes.items(), key=lambda item: -item[1])
    ]

    # Select featured repositories in preferred order with graceful fallback
    repo_map = {r["name"]: r for r in repos}
    featured: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in FEATURED_ORDER:
        if name in repo_map:
            featured.append(repo_map[name])
            seen.add(name)

    if len(featured) < 4:
        fallback = sorted(
            [r for r in repos if r["name"] not in seen and r["name"] != user["login"]],
            key=lambda r: (r["stargazerCount"], r["updatedAt"]),
            reverse=True,
        )
        for r in fallback:
            featured.append(r)
            seen.add(r["name"])
            if len(featured) == 4:
                break

    enriched_featured: list[dict[str, Any]] = []
    for r in featured:
        edges = r["languages"]["edges"]
        repo_total = sum(e["size"] for e in edges) or 1
        primary = (
            r["primaryLanguage"]["name"]
            if r.get("primaryLanguage")
            else (edges[0]["node"]["name"] if edges else "Code")
        )
        primary_bytes = 0
        for e in edges:
            if e["node"]["name"] == primary:
                primary_bytes = e["size"]
                break
        if primary_bytes == 0 and edges:
            primary_bytes = edges[0]["size"]
        primary_pct = round((primary_bytes / repo_total) * 100) if edges else 100
        desc = (
            CURATED_DESCRIPTIONS.get(r["name"])
            or (r.get("description") or "").strip()
            or "Public repository built and maintained on GitHub."
        )
        enriched_featured.append(
            {
                "name": r["name"],
                "url": r["url"],
                "homepage": r.get("homepageUrl") or "",
                "description": desc,
                "stars": r["stargazerCount"],
                "forks": r["forkCount"],
                "updated_rel": relative_time(r["updatedAt"]),
                "primary_lang": primary,
                "primary_pct": primary_pct,
            }
        )

    return {
        "login": user["login"],
        "name": user.get("name") or user["login"],
        "avatar_url": user["avatarUrl"],
        "own_repos": own_repos,
        "total_stars": total_stars,
        "total_contributions": total_contributions,
        "followers": followers,
        "active_days": active_days,
        "busiest_day": busiest["contributionCount"],
        "busiest_date": busiest["date"],
        "longest_streak": longest_streak,
        "current_streak": current_streak,
        "weeks": weeks,
        "languages": sorted_langs,
        "featured": enriched_featured,
    }


def build_dot_matrix_avatar(
    avatar_bytes: bytes,
    cx: float = 660.0,
    cy: float = 138.0,
    radius: float = 96.0,
    grid_size: int = 36,
) -> str:
    """Convert avatar image bytes into a colored circular dot-matrix SVG group."""
    img = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
    img = img.resize((grid_size, grid_size), Image.Resampling.LANCZOS)
    pixels = img.load()

    step = (radius * 2.0) / grid_size
    start_x = cx - radius + step * 0.5
    start_y = cy - radius + step * 0.5
    dots: list[str] = []

    for row in range(grid_size):
        for col in range(grid_size):
            px = start_x + col * step
            py = start_y + row * step
            dist = math.hypot(px - cx, py - cy)
            if dist > radius - 1.5:
                continue

            r, g, b = pixels[col, row]
            # Slight gamma lift (0.85) so dark ember shadows stay legible on #0D0605
            rl = int(min(255, ((r / 255.0) ** 0.85) * 255))
            gl = int(min(255, ((g / 255.0) ** 0.85) * 255))
            bl = int(min(255, ((b / 255.0) ** 0.85) * 255))
            lum = (0.299 * rl + 0.587 * gl + 0.114 * bl) / 255.0

            # Subtle circular vignette near the rim
            edge_fade = max(0.35, min(1.0, (radius - dist) / 14.0))
            dot_r = (0.90 + lum * 1.35) * (0.75 + 0.25 * edge_fade)
            opacity = min(0.98, (0.42 + lum * 0.56) * edge_fade)
            color = f"#{rl:02X}{gl:02X}{bl:02X}"
            dots.append(
                f'<circle cx="{px:.1f}" cy="{py:.1f}" r="{dot_r:.2f}" '
                f'fill="{color}" fill-opacity="{opacity:.2f}"/>'
            )

    return "\n    ".join(dots)


def svg_card_shell(
    height: int,
    defs_extra: str,
    body: str,
    styles: str = "",
) -> str:
    """Wrap card internals in a GitHub-safe 840px Solar Ember SVG shell."""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{SVG_WIDTH}" height="{height}" viewBox="0 0 {SVG_WIDTH} {height}" role="img">
  <defs>
    <linearGradient id="cardBg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#160A08"/>
      <stop offset="55%" stop-color="#0D0605"/>
      <stop offset="100%" stop-color="#1A0B08"/>
    </linearGradient>
    <radialGradient id="emberGlow" cx="78%" cy="22%" r="60%">
      <stop offset="0%" stop-color="#EB7827" stop-opacity="0.22"/>
      <stop offset="45%" stop-color="#A13E24" stop-opacity="0.09"/>
      <stop offset="100%" stop-color="#0D0605" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="borderGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#A13E24" stop-opacity="0.85"/>
      <stop offset="50%" stop-color="#4B231D" stop-opacity="0.7"/>
      <stop offset="100%" stop-color="#EB7827" stop-opacity="0.55"/>
    </linearGradient>
    <filter id="softGlow" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="4" result="blur"/>
      <feMerge>
        <feMergeNode in="blur"/>
        <feMergeNode in="SourceGraphic"/>
      </feMerge>
    </filter>
    <pattern id="gridPattern" width="28" height="28" patternUnits="userSpaceOnUse">
      <path d="M 28 0 L 0 0 0 28" fill="none" stroke="#4B231D" stroke-width="0.6" stroke-opacity="0.26"/>
    </pattern>
    {defs_extra}
  </defs>
  <style>
    .sans {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }}
    @keyframes pulseDot {{
      0%, 100% {{ opacity: 1; transform: scale(1); }}
      50% {{ opacity: 0.45; transform: scale(0.85); }}
    }}
    @keyframes shimmer {{
      0%, 100% {{ opacity: 0.9; }}
      50% {{ opacity: 0.55; }}
    }}
    {styles}
  </style>
  <rect x="1" y="1" width="{SVG_WIDTH - 2}" height="{height - 2}" rx="18" fill="url(#cardBg)" stroke="url(#borderGrad)" stroke-width="1.4"/>
  <rect x="2" y="2" width="{SVG_WIDTH - 4}" height="{height - 4}" rx="17" fill="url(#gridPattern)" opacity="0.75"/>
  <rect x="2" y="2" width="{SVG_WIDTH - 4}" height="{height - 4}" rx="17" fill="url(#emberGlow)"/>
  {body}
</svg>
'''


def render_hero_svg(stats: dict[str, Any], avatar_bytes: bytes) -> str:
    """Render assets/hero-living-identity.svg (840x350)."""
    height = 350
    dot_matrix = build_dot_matrix_avatar(avatar_bytes, cx=660.0, cy=142.0, radius=92.0, grid_size=36)

    top_langs = [l["name"] for l in stats["languages"][:4]]
    if not top_langs:
        top_langs = ["JavaScript", "Python", "TypeScript", "Rust"]

    pill_svg: list[str] = []
    px = 40
    for idx, lang in enumerate(top_langs):
        label = esc(lang)
        w = max(72, len(lang) * 8 + 28)
        dot_color = SPECTRUM_COLORS[idx % len(SPECTRUM_COLORS)]
        pill_svg.append(
            f'<g transform="translate({px}, 224)">'
            f'<rect width="{w}" height="28" rx="14" fill="#210E0B" stroke="#6C312B" stroke-width="1"/>'
            f'<circle cx="14" cy="14" r="3.5" fill="{dot_color}"/>'
            f'<text x="24" y="18.5" class="sans" font-size="12" font-weight="600" fill="#E7C8B5">{label}</text>'
            f'</g>'
        )
        px += w + 10

    telemetry_cols = [
        ("REPOSITORIES", stats["own_repos"], "public owned"),
        ("TOTAL STARS", stats["total_stars"], "community signal"),
        ("CONTRIBUTIONS", stats["total_contributions"], "last 365 days"),
        ("FOLLOWERS", stats["followers"], "network reach"),
    ]
    strip_svg: list[str] = []
    col_w = (SVG_WIDTH - 80) / 4
    for i, (label, val, sub) in enumerate(telemetry_cols):
        cx = 40 + i * col_w
        if i > 0:
            strip_svg.append(
                f'<line x1="{cx:.1f}" y1="286" x2="{cx:.1f}" y2="332" stroke="#4B231D" stroke-width="1" stroke-opacity="0.7"/>'
            )
        tx = cx + (18 if i > 0 else 0)
        val_str = str(val)
        sub_dx = len(val_str) * 14 + 12
        val_color = "#FDFE75" if i == 2 else "#FFF7ED"
        strip_svg.append(
            f'<text x="{tx:.1f}" y="299" class="mono" font-size="10" letter-spacing="1.4" fill="#A67C6B">{label}</text>'
            f'<text x="{tx:.1f}" y="325" class="sans" font-size="22" font-weight="800" fill="{val_color}">{esc(val_str)}</text>'
            f'<text x="{tx + sub_dx:.1f}" y="324" class="sans" font-size="11" fill="#A67C6B">{esc(sub)}</text>'
        )

    defs_extra = """
    <linearGradient id="wordmarkTop" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#FFF7ED"/>
      <stop offset="52%" stop-color="#F8E543"/>
      <stop offset="100%" stop-color="#FDFE75"/>
    </linearGradient>
    <radialGradient id="avatarHalo" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#EB7827" stop-opacity="0.30"/>
      <stop offset="65%" stop-color="#C65428" stop-opacity="0.12"/>
      <stop offset="100%" stop-color="#0D0605" stop-opacity="0"/>
    </radialGradient>
    <clipPath id="portraitClip">
      <circle cx="660" cy="142" r="95"/>
    </clipPath>
    <linearGradient id="scanSweep" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#FDFE75" stop-opacity="0"/>
      <stop offset="50%" stop-color="#FDFE75" stop-opacity="0.22"/>
      <stop offset="100%" stop-color="#FDFE75" stop-opacity="0"/>
    </linearGradient>
    """

    display_name = esc(stats["name"])
    login = esc(stats["login"])
    badge_text = f"@{login}"
    badge_w = max(96, int(len(badge_text) * 6.8 + 34))
    badge_x = 800 - badge_w
    dot_cx = badge_x + 16

    styles = f"""
    @keyframes orbitSpin {{
      from {{ transform: rotate(0deg); }}
      to {{ transform: rotate(360deg); }}
    }}
    @keyframes scanMove {{
      0% {{ transform: translateY(-95px); }}
      100% {{ transform: translateY(190px); }}
    }}
    .orbit-slow {{ transform-origin: 660px 142px; animation: orbitSpin 28s linear infinite; }}
    .orbit-rev {{ transform-origin: 660px 142px; animation: orbitSpin 20s linear infinite reverse; }}
    .scan-bar {{ animation: scanMove 4.8s ease-in-out infinite alternate; }}
    .live-dot {{ transform-origin: {dot_cx}px 31px; animation: pulseDot 2.2s ease-in-out infinite; }}
    """

    body = f'''
  <!-- Top System Header -->
  <text x="40" y="35" class="mono" font-size="10.5" letter-spacing="2.2" fill="#A67C6B">SYSTEM // LIVING IDENTITY</text>
  <g>
    <rect x="{badge_x}" y="19" width="{badge_w}" height="24" rx="12" fill="#210E0B" stroke="#6C312B" stroke-width="1"/>
    <circle cx="{dot_cx}" cy="31" r="4" fill="#F8E543" class="live-dot" filter="url(#softGlow)"/>
    <text x="{dot_cx + 11}" y="34.5" class="mono" font-size="10.5" font-weight="700" fill="#FFF7ED">{badge_text}</text>
  </g>

  <!-- Left 3D Layered Wordmark & Bio -->
  <text x="40" y="78" class="mono" font-size="11.5" letter-spacing="2.4" fill="#EDA330">HELLO, WORLD. I'M</text>
  <path d="M 40 88 Q 210 68 380 86" fill="none" stroke="#EB7827" stroke-width="1.2" stroke-opacity="0.45"/>

  <!-- 3D Extruded Wordmark Layers -->
  <text x="45" y="147" class="sans" font-size="60" font-weight="900" letter-spacing="-1.5" fill="#4B231D">{display_name}</text>
  <text x="43" y="145" class="sans" font-size="60" font-weight="900" letter-spacing="-1.5" fill="#A13E24">{display_name}</text>
  <text x="41.5" y="143.5" class="sans" font-size="60" font-weight="900" letter-spacing="-1.5" fill="#EB7827">{display_name}</text>
  <text x="40" y="142" class="sans" font-size="60" font-weight="900" letter-spacing="-1.5" fill="url(#wordmarkTop)">{display_name}</text>

  <text x="40" y="176" class="sans" font-size="15.5" font-weight="600" fill="#FFF7ED">Full-Stack &amp; Systems Developer</text>
  <text x="40" y="199" class="sans" font-size="13" fill="#E7C8B5">Building desktop tools, web apps, and automation in public.</text>

  <!-- Language Pills -->
  {"".join(pill_svg)}

  <!-- Right Dot-Matrix Avatar inside Concentric Radar Rings -->
  <circle cx="660" cy="142" r="118" fill="url(#avatarHalo)"/>
  <circle cx="660" cy="142" r="112" fill="none" stroke="#4B231D" stroke-width="1" stroke-dasharray="4 6" class="orbit-slow"/>
  <circle cx="660" cy="142" r="102" fill="none" stroke="#A13E24" stroke-width="1.2" stroke-dasharray="18 8 4 8" class="orbit-rev"/>
  <circle cx="660" cy="142" r="95" fill="#1B0908" stroke="#EB7827" stroke-width="1" stroke-opacity="0.55"/>

  <g clip-path="url(#portraitClip)">
    {dot_matrix}
    <rect x="560" y="128" width="200" height="22" fill="url(#scanSweep)" class="scan-bar"/>
  </g>

  <!-- Bottom Live Telemetry Strip -->
  <line x1="40" y1="274" x2="800" y2="274" stroke="#4B231D" stroke-width="1"/>
  {"".join(strip_svg)}
'''
    return svg_card_shell(height, defs_extra, body, styles)


def iso_project(wx: float, wy: float, wz: float, ox: float, oy: float, sx: float, sy: float) -> tuple[float, float]:
    """Project 3D grid coordinates (week, day, height) to 2D isometric space."""
    ix = ox + (wx - wy) * sx
    iy = oy + (wx + wy) * sy - wz
    return ix, iy


def render_contribution_city_svg(stats: dict[str, Any]) -> str:
    """Render assets/contribution-city.svg (840x380) with mathematically fitted isometric 3D skyline."""
    height = 380
    weeks = stats["weeks"]
    num_weeks = len(weeks)
    max_count = max(1, stats["busiest_day"])

    # Color levels strictly matched to Solar Ember palette
    level_colors = {
        0: ("#1F0E0C", "#180A08", "#130806", "#3A1A15"),
        1: ("#8E4034", "#632922", "#421915", "#A13E24"),
        2: ("#DF6B32", "#B34822", "#7D2D16", "#EB7827"),
        3: ("#FFA138", "#EB7827", "#A84912", "#EDA330"),
        4: ("#FDFE75", "#F8E543", "#D1821B", "#FDFE75"),
    }

    def get_level(cnt: int) -> int:
        if cnt <= 0:
            return 0
        ratio = cnt / max_count
        if ratio <= 0.25:
            return 1
        if ratio <= 0.50:
            return 2
        if ratio <= 0.78:
            return 3
        return 4

    # Unit isometric steps before auto-fit scaling
    base_sx = 8.4
    base_sy = 4.2
    tile_gap = 0.84
    max_h = 56.0

    # Collect raw polygons & compute exact bounding box to guarantee zero clipping
    buildings: list[dict[str, Any]] = []
    min_x, max_x = float("inf"), float("-inf")
    min_y, max_y = float("inf"), float("-inf")

    for w_idx, week in enumerate(weeks):
        for d_idx, day in enumerate(week["contributionDays"]):
            cnt = day["contributionCount"]
            lvl = get_level(cnt)
            h_val = 2.0 if cnt == 0 else (7.0 + (cnt / max_count) * max_h)

            x0, x1 = w_idx, w_idx + tile_gap
            y0, y1 = d_idx, d_idx + tile_gap

            pts = [
                iso_project(x0, y0, h_val, 0, 0, base_sx, base_sy),
                iso_project(x1, y0, h_val, 0, 0, base_sx, base_sy),
                iso_project(x1, y1, h_val, 0, 0, base_sx, base_sy),
                iso_project(x0, y1, h_val, 0, 0, base_sx, base_sy),
                iso_project(x1, y1, 0, 0, 0, base_sx, base_sy),
                iso_project(x0, y1, 0, 0, 0, base_sx, base_sy),
                iso_project(x1, y0, 0, 0, 0, base_sx, base_sy),
            ]
            for px, py in pts:
                min_x = min(min_x, px)
                max_x = max(max_x, px)
                min_y = min(min_y, py)
                max_y = max(max_y, py)

            buildings.append(
                {
                    "w": w_idx,
                    "d": d_idx,
                    "h": h_val,
                    "lvl": lvl,
                    "cnt": cnt,
                }
            )

    # Fit cleanly inside target viewport box x=[276..808], y=[36..348]
    target_x0, target_x1 = 276.0, 808.0
    target_y0, target_y1 = 38.0, 348.0
    raw_w = max(1.0, max_x - min_x)
    raw_h = max(1.0, max_y - min_y)
    scale = min((target_x1 - target_x0) / raw_w, (target_y1 - target_y0) / raw_h)
    shift_x = target_x0 + ((target_x1 - target_x0) - raw_w * scale) * 0.5 - min_x * scale
    shift_y = target_y0 + ((target_y1 - target_y0) - raw_h * scale) * 0.5 - min_y * scale

    def xform(pt: tuple[float, float]) -> str:
        return f"{pt[0] * scale + shift_x:.1f},{pt[1] * scale + shift_y:.1f}"

    def poly_str(coords: list[tuple[float, float]]) -> str:
        return " ".join(xform(c) for c in coords)

    # Painter's order: back to front
    buildings.sort(key=lambda b: (b["w"] + b["d"], b["w"]))

    city_polys: list[str] = []
    for b in buildings:
        w, d, h_val, lvl = b["w"], b["d"], b["h"], b["lvl"]
        x0, x1 = w, w + tile_gap
        y0, y1 = d, d + tile_gap
        top_c, left_c, right_c, stroke_c = level_colors[lvl]

        t0 = iso_project(x0, y0, h_val, 0, 0, base_sx, base_sy)
        t1 = iso_project(x1, y0, h_val, 0, 0, base_sx, base_sy)
        t2 = iso_project(x1, y1, h_val, 0, 0, base_sx, base_sy)
        t3 = iso_project(x0, y1, h_val, 0, 0, base_sx, base_sy)

        b1 = iso_project(x1, y0, 0, 0, 0, base_sx, base_sy)
        b2 = iso_project(x1, y1, 0, 0, 0, base_sx, base_sy)
        b3 = iso_project(x0, y1, 0, 0, 0, base_sx, base_sy)

        cls_attr = ' class="peak-tower"' if lvl == 4 else ""
        if lvl == 0:
            city_polys.append(
                f'<polygon points="{poly_str([t0, t1, t2, t3])}" '
                f'fill="{top_c}" stroke="{stroke_c}" stroke-width="0.55"/>'
            )
        else:
            city_polys.append(
                f'<g{cls_attr}>'
                f'<polygon points="{poly_str([t3, t2, b2, b3])}" fill="{left_c}" stroke="{stroke_c}" stroke-width="0.45"/>'
                f'<polygon points="{poly_str([t2, t1, b1, b2])}" fill="{right_c}" stroke="{stroke_c}" stroke-width="0.45"/>'
                f'<polygon points="{poly_str([t0, t1, t2, t3])}" fill="{top_c}" stroke="{stroke_c}" stroke-width="0.5"/>'
                f'</g>'
            )

    swatches = [
        level_colors[0][0],
        level_colors[1][0],
        level_colors[2][0],
        level_colors[3][0],
        level_colors[4][0],
    ]
    legend_svg = "".join(
        f'<rect x="{74 + i * 16}" y="317" width="11" height="11" rx="2.5" fill="{c}" stroke="#4B231D" stroke-width="0.7"/>'
        for i, c in enumerate(swatches)
    )

    styles = """
    @keyframes peakPulse {
      0%, 100% { opacity: 1; }
      50% { opacity: 0.78; }
    }
    .peak-tower { animation: peakPulse 3s ease-in-out infinite; }
    """

    body = f'''
  <!-- Left City HUD -->
  <text x="40" y="42" class="mono" font-size="10.5" letter-spacing="1.8" fill="#EDA330">CONTRIBUTION CITY // 365D</text>
  <text x="40" y="96" class="sans" font-size="46" font-weight="900" fill="#F8E543">{esc(stats["total_contributions"])}</text>
  <text x="40" y="120" class="sans" font-size="13.5" font-weight="600" fill="#FFF7ED">contributions in the last year</text>

  <line x1="40" y1="142" x2="232" y2="142" stroke="#4B231D" stroke-width="1"/>

  <g transform="translate(40, 166)">
    <circle cx="5" cy="-4" r="3.5" fill="#EB7827"/>
    <text x="16" y="0" class="sans" font-size="13" fill="#E7C8B5"><tspan font-weight="700" fill="#FFF7ED">{esc(stats["active_days"])}</tspan> active days</text>

    <circle cx="5" cy="24" r="3.5" fill="#F8E543"/>
    <text x="16" y="28" class="sans" font-size="13" fill="#E7C8B5">Busiest day: <tspan font-weight="700" fill="#FFF7ED">{esc(stats["busiest_day"])}</tspan></text>

    <circle cx="5" cy="52" r="3.5" fill="#C65428"/>
    <text x="16" y="56" class="sans" font-size="13" fill="#E7C8B5">Longest streak: <tspan font-weight="700" fill="#FFF7ED">{esc(stats["longest_streak"])}d</tspan></text>
  </g>

  <text x="40" y="326.5" class="mono" font-size="10" fill="#A67C6B">Less</text>
  {legend_svg}
  <text x="160" y="326.5" class="mono" font-size="10" fill="#A67C6B">More</text>
  <text x="40" y="350" class="mono" font-size="9.5" fill="#6C312B">@{esc(stats["login"])} · {num_weeks}-week isometric skyline</text>

  <!-- Right Isometric 3D Skyline -->
  <g>
    {"".join(city_polys)}
  </g>
'''
    return svg_card_shell(height, "", body, styles)


def render_profile_signal_svg(stats: dict[str, Any]) -> str:
    """Render assets/profile-signal.svg (840x178)."""
    height = 178
    tiles = [
        ("REPOSITORIES", stats["own_repos"], "owned public", min(1.0, stats["own_repos"] / 25.0), "#EB7827"),
        ("TOTAL STARS", stats["total_stars"], "repo appreciation", min(1.0, max(0.18, stats["total_stars"] / 20.0)), "#EDA330"),
        ("CONTRIBUTIONS", stats["total_contributions"], "365d activity", min(1.0, stats["total_contributions"] / 300.0), "#F8E543"),
        ("ACTIVE DAYS", stats["active_days"], f"streak {stats['longest_streak']}d max", min(1.0, stats["active_days"] / 120.0), "#C65428"),
    ]

    tile_w = 182
    gap = 12
    start_x = 38
    track_w = tile_w - 28
    tiles_svg: list[str] = []

    for i, (label, val, sub, ratio, accent) in enumerate(tiles):
        tx = start_x + i * (tile_w + gap)
        fill_w = max(18.0, track_w * ratio)
        tiles_svg.append(
            f'<g transform="translate({tx}, 64)">'
            f'<rect width="{tile_w}" height="92" rx="12" fill="#210E0B" stroke="#4B231D" stroke-width="1"/>'
            f'<text x="14" y="23" class="mono" font-size="10" letter-spacing="1.2" fill="#A67C6B">{label}</text>'
            f'<text x="14" y="55" class="sans" font-size="28" font-weight="800" fill="{accent}">{esc(val)}</text>'
            f'<text x="14" y="70" class="sans" font-size="10.5" fill="#E7C8B5" opacity="0.85">{esc(sub)}</text>'
            f'<rect x="14" y="79" width="{track_w}" height="4" rx="2" fill="#2E1410"/>'
            f'<rect x="14" y="79" width="{fill_w:.1f}" height="4" rx="2" fill="{accent}" filter="url(#softGlow)"/>'
            f'<circle cx="{14 + fill_w:.1f}" cy="81" r="3" fill="#FDFE75"/>'
            f'</g>'
        )

    body = f'''
  <text x="38" y="30" class="mono" font-size="10" letter-spacing="1.8" fill="#EDA330">PROFILE SIGNAL</text>
  <text x="38" y="51" class="sans" font-size="19" font-weight="800" fill="#FFF7ED">Momentum, made visible</text>
  <g transform="translate(690, 22)">
    <rect width="112" height="24" rx="12" fill="#210E0B" stroke="#6C312B" stroke-width="1"/>
    <circle cx="14" cy="12" r="3.5" fill="#F8E543"/>
    <text x="24" y="15.5" class="mono" font-size="9.5" font-weight="700" fill="#E7C8B5">LIVE TELEMETRY</text>
  </g>
  {"".join(tiles_svg)}
'''
    return svg_card_shell(height, "", body)


def render_tech_spectrum_svg(stats: dict[str, Any]) -> str:
    """Render assets/tech-spectrum.svg (840x255)."""
    height = 255
    langs = stats["languages"][:6]
    if not langs:
        langs = [{"name": "JavaScript", "pct": 100.0}]

    # Stacked full-width spectrum bar (x=38..802, width=764)
    bar_total_w = 764.0
    total_top_pct = sum(l["pct"] for l in langs) or 100.0
    bar_segments: list[str] = []
    cur_x = 38.0
    for i, l in enumerate(langs):
        seg_w = (l["pct"] / total_top_pct) * bar_total_w
        color = SPECTRUM_COLORS[i % len(SPECTRUM_COLORS)]
        bar_segments.append(
            f'<rect x="{cur_x:.1f}" y="68" width="{max(4.0, seg_w - 2):.1f}" height="10" rx="3" fill="{color}"/>'
        )
        cur_x += seg_w

    # 2 columns x 3 rows grid
    cols_svg: list[str] = []
    col_w = 372
    row_h = 46
    for i, l in enumerate(langs):
        col = i % 2
        row = i // 2
        gx = 38 + col * (col_w + 20)
        gy = 98 + row * row_h
        color = SPECTRUM_COLORS[i % len(SPECTRUM_COLORS)]
        pct_text = f"{l['pct']:.1f}%"
        fill_w = max(10.0, 260.0 * min(1.0, l["pct"] / 100.0))

        cols_svg.append(
            f'<g transform="translate({gx}, {gy})">'
            f'<rect width="{col_w}" height="38" rx="9" fill="#210E0B" stroke="#4B231D" stroke-width="0.9"/>'
            f'<circle cx="16" cy="14" r="4.2" fill="{color}"/>'
            f'<text x="28" y="18" class="sans" font-size="13" font-weight="700" fill="#FFF7ED">{esc(l["name"])}</text>'
            f'<text x="{col_w - 14}" y="18" text-anchor="end" class="mono" font-size="11.5" font-weight="700" fill="{color}">{pct_text}</text>'
            f'<rect x="14" y="27" width="{col_w - 28}" height="4" rx="2" fill="#2E1410"/>'
            f'<rect x="14" y="27" width="{min(col_w - 28, fill_w * 1.32):.1f}" height="4" rx="2" fill="{color}"/>'
            f'</g>'
        )

    styles = """
    @keyframes blinkCursor {
      0%, 49% { opacity: 1; }
      50%, 100% { opacity: 0; }
    }
    .cursor-blink { animation: blinkCursor 1.1s infinite; }
    """

    body = f'''
  <text x="38" y="30" class="mono" font-size="10" letter-spacing="1.8" fill="#EDA330">TECHNOLOGY SPECTRUM</text>
  <text x="38" y="51" class="sans" font-size="19" font-weight="800" fill="#FFF7ED">Language Stack</text>
  <text x="790" y="47" text-anchor="end" class="mono" font-size="12" fill="#E7C8B5">&gt; stack.scan</text>
  <rect x="794" y="36" width="7" height="13" fill="#F8E543" class="cursor-blink"/>

  <!-- Stacked Spectrum Bar -->
  <rect x="38" y="68" width="764" height="10" rx="5" fill="#210E0B"/>
  {"".join(bar_segments)}

  <!-- 2x3 Language Tiles -->
  {"".join(cols_svg)}
'''
    return svg_card_shell(height, "", body, styles)


def render_projects_showcase_svg(stats: dict[str, Any]) -> str:
    """Render assets/projects-showcase.svg (840x375) with 2x2 terminal cards and donut rings."""
    height = 375
    featured = stats["featured"][:4]
    cards_svg: list[str] = []

    card_w = 372
    card_h = 136
    radius = 22.0
    circumference = 2.0 * math.pi * radius

    for i, repo in enumerate(featured):
        col = i % 2
        row = i // 2
        cx = 38 + col * (card_w + 20)
        cy = 68 + row * (card_h + 14)
        accent = SPECTRUM_COLORS[i % len(SPECTRUM_COLORS)]

        lines = textwrap.wrap(repo["description"], width=38)[:2]
        line1 = esc(lines[0]) if len(lines) > 0 else ""
        line2 = esc(lines[1]) if len(lines) > 1 else ""

        pct = max(5, min(100, int(repo["primary_pct"])))
        dash_offset = circumference * (1.0 - pct / 100.0)

        cards_svg.append(
            f'<g transform="translate({cx}, {cy})">'
            f'<rect width="{card_w}" height="{card_h}" rx="12" fill="#210E0B" stroke="#4B231D" stroke-width="1"/>'
            f'<line x1="14" y1="24" x2="{card_w - 14}" y2="24" stroke="#4B231D" stroke-width="0.7" stroke-opacity="0.6"/>'
            f'<circle cx="18" cy="13" r="3" fill="{accent}"/>'
            f'<text x="27" y="16.5" class="mono" font-size="10" fill="#A67C6B">./{esc(repo["name"])}</text>'
            f'<text x="16" y="47" class="sans" font-size="16" font-weight="800" fill="#FFF7ED">{esc(repo["name"])}</text>'
            f'<text x="16" y="67" class="sans" font-size="11.5" fill="#E7C8B5">{line1}</text>'
            f'<text x="16" y="83" class="sans" font-size="11.5" fill="#E7C8B5">{line2}</text>'
            f'<rect x="16" y="100" width="{max(54, len(repo["primary_lang"]) * 7 + 20)}" height="22" rx="11" fill="#2E1410" stroke="#6C312B" stroke-width="0.8"/>'
            f'<text x="26" y="114.5" class="mono" font-size="10.5" font-weight="700" fill="{accent}">{esc(repo["primary_lang"])}</text>'
            f'<text x="{max(82, len(repo["primary_lang"]) * 7 + 46)}" y="114.5" class="mono" font-size="10.5" fill="#A67C6B">★ {repo["stars"]} · {esc(repo["updated_rel"])}</text>'
            f'<g transform="translate({card_w - 48}, 74)">'
            f'<circle cx="0" cy="0" r="{radius}" fill="none" stroke="#2E1410" stroke-width="5"/>'
            f'<circle cx="0" cy="0" r="{radius}" fill="none" stroke="{accent}" stroke-width="5" '
            f'stroke-linecap="round" stroke-dasharray="{circumference:.2f}" stroke-dashoffset="{dash_offset:.2f}" '
            f'transform="rotate(-90)"/>'
            f'<text x="0" y="4" text-anchor="middle" class="mono" font-size="11" font-weight="700" fill="#FFF7ED">{pct}%</text>'
            f'</g>'
            f'</g>'
        )

    body = f'''
  <!-- Terminal Window Header -->
  <circle cx="46" cy="34" r="5" fill="#C65428"/>
  <circle cx="64" cy="34" r="5" fill="#EB7827"/>
  <circle cx="82" cy="34" r="5" fill="#F8E543"/>
  <text x="104" y="38" class="mono" font-size="12" font-weight="700" letter-spacing="1.2" fill="#FFF7ED">PROJECTS.LIST</text>
  <text x="234" y="38" class="mono" font-size="11.5" fill="#A67C6B">./projects.sh --featured</text>
  <text x="802" y="38" text-anchor="end" class="mono" font-size="11" fill="#EDA330">{len(featured)} landmarks</text>
  <line x1="38" y1="52" x2="802" y2="52" stroke="#4B231D" stroke-width="1"/>

  <!-- 2x2 Featured Project Cards -->
  {"".join(cards_svg)}
'''
    return svg_card_shell(height, "", body)


def render_open_channel_svg(stats: dict[str, Any]) -> str:
    """Render assets/open-channel.svg (840x168)."""
    height = 168
    styles = """
    @keyframes radarSweep {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }
    .radar-arm { transform-origin: 720px 84px; animation: radarSweep 9s linear infinite; }
    """
    defs_extra = """
    <linearGradient id="radarCone" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" stop-color="#F8E543" stop-opacity="0.45"/>
      <stop offset="100%" stop-color="#EB7827" stop-opacity="0"/>
    </linearGradient>
    """
    pills = [
        (f"@{stats['login']}", "#F8E543"),
        ("discord.gg/drexxsss", "#EB7827"),
        ("rafifsyandana@proton.me", "#EDA330"),
    ]
    pill_svg: list[str] = []
    px = 38
    for text, color in pills:
        w = len(text) * 6.8 + 28
        pill_svg.append(
            f'<g transform="translate({px}, 110)">'
            f'<rect width="{w:.1f}" height="28" rx="14" fill="#210E0B" stroke="#6C312B" stroke-width="1"/>'
            f'<circle cx="13" cy="14" r="3" fill="{color}"/>'
            f'<text x="22" y="17.5" class="mono" font-size="10.5" fill="#FFF7ED">{esc(text)}</text>'
            f'</g>'
        )
        px += int(w) + 10

    body = f'''
  <text x="38" y="34" class="mono" font-size="10" letter-spacing="1.8" fill="#EDA330">OPEN CHANNEL // SIGNAL ACTIVE</text>
  <text x="38" y="64" class="sans" font-size="24" font-weight="800" fill="#FFF7ED">Build the next chapter</text>
  <text x="38" y="88" class="sans" font-size="13" fill="#E7C8B5">Open to collaborations, systems tooling, web apps, and creative engineering.</text>
  {"".join(pill_svg)}

  <!-- Right Concentric Ember Radar -->
  <circle cx="720" cy="84" r="62" fill="#1B0908" stroke="#4B231D" stroke-width="1"/>
  <circle cx="720" cy="84" r="46" fill="none" stroke="#6C312B" stroke-width="1" stroke-dasharray="4 4"/>
  <circle cx="720" cy="84" r="28" fill="none" stroke="#A13E24" stroke-width="1"/>
  <line x1="652" y1="84" x2="788" y2="84" stroke="#4B231D" stroke-width="0.8"/>
  <line x1="720" y1="16" x2="720" y2="152" stroke="#4B231D" stroke-width="0.8"/>
  <g class="radar-arm">
    <path d="M 720 84 L 774 52 A 62 62 0 0 1 782 84 Z" fill="url(#radarCone)"/>
    <line x1="720" y1="84" x2="782" y2="84" stroke="#F8E543" stroke-width="1.6"/>
  </g>
  <circle cx="720" cy="84" r="6" fill="#FDFE75" filter="url(#softGlow)"/>
'''
    return svg_card_shell(height, defs_extra, body, styles)


def sync_readme_markers(stats: dict[str, Any]) -> None:
    """Update STATS and PROJECTS blocks inside README.md if markers exist."""
    if not README_PATH.exists():
        return
    content = README_PATH.read_text(encoding="utf-8")

    stats_block = (
        "<!-- STATS:START -->\n"
        '<div align="center">\n\n'
        "| Followers | Repositories | Stars | Contributions | Active Days | Longest Streak |\n"
        "|:---:|:---:|:---:|:---:|:---:|:---:|\n"
        f"| **{stats['followers']}** | **{stats['own_repos']}** | **{stats['total_stars']}** | **{stats['total_contributions']}** | **{stats['active_days']}** | **{stats['longest_streak']}d** |\n\n"
        "</div>\n"
        "<!-- STATS:END -->"
    )

    featured = stats["featured"][:4]
    headers = " | ".join(
        f"[{r['name']}]({r['url']})" for r in featured
    )
    aligns = " | ".join([":---"] * len(featured))
    bodies = " | ".join(
        f"{r['description']}<br><br>`{r['primary_lang']}` · ★ {r['stars']} · {r['updated_rel']}"
        for r in featured
    )
    projects_block = (
        "<!-- PROJECTS:START -->\n"
        f"| {headers} |\n"
        f"| {aligns} |\n"
        f"| {bodies} |\n"
        "<!-- PROJECTS:END -->"
    )

    updated = re.sub(
        r"<!-- STATS:START -->.*?<!-- STATS:END -->",
        stats_block,
        content,
        flags=re.DOTALL,
    )
    updated = re.sub(
        r"<!-- PROJECTS:START -->.*?<!-- PROJECTS:END -->",
        projects_block,
        updated,
        flags=re.DOTALL,
    )
    if updated != content:
        README_PATH.write_text(updated, encoding="utf-8")


def main() -> None:
    username = os.environ.get("GITHUB_USERNAME", DEFAULT_USERNAME).strip() or DEFAULT_USERNAME
    token = get_github_token()
    user = fetch_github_data(username, token)
    stats = analyze_profile(user)
    avatar_bytes = fetch_avatar_bytes(stats["avatar_url"])

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    outputs = {
        "hero-living-identity.svg": render_hero_svg(stats, avatar_bytes),
        "contribution-city.svg": render_contribution_city_svg(stats),
        "profile-signal.svg": render_profile_signal_svg(stats),
        "tech-spectrum.svg": render_tech_spectrum_svg(stats),
        "projects-showcase.svg": render_projects_showcase_svg(stats),
        "open-channel.svg": render_open_channel_svg(stats),
    }
    for fname, svg_text in outputs.items():
        path = ASSETS_DIR / fname
        path.write_text(svg_text, encoding="utf-8")
        print(f"Wrote {path.relative_to(ROOT)} ({len(svg_text)} bytes)")

    sync_readme_markers(stats)
    print("Synced README.md markers.")


if __name__ == "__main__":
    main()
