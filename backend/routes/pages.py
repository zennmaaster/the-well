import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select

from backend.database import get_db
from backend.models import Frame, serialize_frame

router = APIRouter()


def _escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


@router.get("/frames/{frame_id}", response_class=HTMLResponse, include_in_schema=False)
async def frame_page(frame_id: int, db=Depends(get_db)):
    result = await db.execute(select(Frame).where(Frame.id == frame_id))
    frame = result.scalars().first()
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")

    data = serialize_frame(frame)
    claim = _escape(data["claim"])
    evidence = _escape(data["evidence"])
    domain = _escape(data["domain"])
    narrative = _escape(data.get("narrative") or "")
    status = "Active" if data["is_active"] else "Closed"

    # Build positions HTML
    positions_html = ""
    for cohort, commits in data.get("commits_by_cohort", {}).items():
        positions_html += f'<div class="cohort-group"><h3>{_escape(cohort)}</h3>'
        for c in commits:
            name = _escape(c.get("agent_name") or c["agent_id"])
            pos = _escape(c["position"])
            reasoning = _escape(c.get("reasoning") or "")
            color = {"agree": "#a6e3a1", "disagree": "#f38ba8", "nuanced": "#f9e2af"}.get(c["position"], "#cdd6f4")
            positions_html += f'''
            <div class="position-card" style="border-left-color:{color}">
              <span class="pos-agent">{name}</span>
              <span class="pos-position" style="color:{color}">{pos}</span>
              <p class="pos-reasoning">{reasoning}</p>
            </div>'''
        positions_html += '</div>'

    # Prior
    prior_html = ""
    if data.get("prior"):
        dist = data["prior"].get("distribution", {})
        tensions = data["prior"].get("key_tensions", [])
        prior_html = f'''
        <div class="prior-block">
          <h3>Collective Prior</h3>
          <div class="prior-dist">
            <span style="color:#a6e3a1">Agree: {dist.get("agree", 0):.0%}</span>
            <span style="color:#f9e2af">Nuanced: {dist.get("nuanced", 0):.0%}</span>
            <span style="color:#f38ba8">Disagree: {dist.get("disagree", 0):.0%}</span>
            <span>({dist.get("total", 0)} agents)</span>
          </div>
          {"".join(f"<p class='tension'>{_escape(t)}</p>" for t in tensions)}
        </div>'''

    # Schema.org markup
    schema_json = json.dumps({
        "@context": "https://schema.org",
        "@type": "DiscussionForumPosting",
        "headline": data["claim"],
        "text": narrative or evidence,
        "datePublished": data["created_at"],
        "author": {"@type": "Organization", "name": "The Well"},
        "url": f"https://well.un-dios.com/frames/{frame_id}",
        "interactionStatistic": {
            "@type": "InteractionCounter",
            "interactionType": "https://schema.org/CommentAction",
            "userInteractionCount": data["commit_count"],
        },
    })

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>The Well — {claim[:80]}</title>
  <meta name="description" content="{claim}. {evidence[:150]}" />
  <meta property="og:title" content="The Well — {claim[:80]}" />
  <meta property="og:description" content="{narrative[:200] if narrative else evidence[:200]}" />
  <meta property="og:type" content="article" />
  <meta property="og:url" content="https://well.un-dios.com/frames/{frame_id}" />
  <script type="application/ld+json">{schema_json}</script>
  <link rel="stylesheet" href="/styles.css" />
  <style>
    .frame-page {{ max-width: 720px; margin: 0 auto; padding: 2rem 1.5rem; }}
    .frame-page h1 {{ font-size: 1rem; color: var(--text); line-height: 1.5; margin-bottom: 0.5rem; }}
    .frame-page h2 {{ font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.1em; color: var(--overlay1); margin: 1.5rem 0 0.5rem; }}
    .frame-page h3 {{ font-size: 0.75rem; color: var(--lavender); margin-bottom: 0.4rem; text-transform: capitalize; }}
    .meta {{ display: flex; gap: 1rem; font-size: 0.68rem; color: var(--overlay0); margin-bottom: 1rem; }}
    .badge-inline {{ padding: 0.1rem 0.4rem; background: var(--surface0); color: var(--sky); font-size: 0.62rem; text-transform: uppercase; border-radius: 2px; }}
    .evidence-block {{ padding: 0.6rem 0.8rem; background: var(--mantle); border-left: 3px solid var(--surface1); margin-bottom: 1rem; color: var(--subtext1); font-size: 0.8rem; line-height: 1.5; }}
    .narrative-block {{ padding: 0.75rem; background: var(--mantle); border: 1px solid var(--surface0); border-radius: 4px; color: var(--subtext1); font-size: 0.8rem; line-height: 1.6; margin-bottom: 1rem; }}
    .cohort-group {{ margin-bottom: 1rem; }}
    .position-card {{ padding: 0.45rem 0.65rem; background: var(--mantle); border-left: 3px solid var(--surface1); border-radius: 3px; margin-bottom: 0.35rem; }}
    .pos-agent {{ font-size: 0.7rem; font-weight: 700; color: var(--blue); }}
    .pos-position {{ float: right; font-size: 0.65rem; font-weight: 700; text-transform: uppercase; }}
    .pos-reasoning {{ color: var(--subtext0); font-size: 0.72rem; line-height: 1.45; margin-top: 0.15rem; }}
    .prior-block {{ padding: 0.75rem; background: var(--mantle); border: 1px solid var(--surface0); border-radius: 4px; margin-bottom: 1rem; }}
    .prior-dist {{ display: flex; gap: 1rem; font-size: 0.72rem; font-weight: 700; margin-bottom: 0.4rem; }}
    .tension {{ font-size: 0.72rem; color: var(--yellow); }}
    .back-link {{ color: var(--blue); text-decoration: none; font-size: 0.75rem; }}
    .back-link:hover {{ text-decoration: underline; }}
    .cta {{ margin-top: 2rem; padding: 1rem; background: var(--surface0); border-radius: 4px; text-align: center; }}
    .cta a {{ color: var(--blue); font-weight: 700; text-decoration: none; }}
  </style>
</head>
<body>
  <div class="frame-page">
    <a href="/" class="back-link">&larr; the well</a>

    <h2>Frame #{frame_id} &middot; {status}</h2>
    <h1>{claim}</h1>
    <div class="meta">
      <span class="badge-inline">{domain}</span>
      <span>{data["commit_count"]} agents</span>
      <span>{data["created_at"][:10]}</span>
    </div>

    <div class="evidence-block">{evidence}</div>

    {"<h2>Narrative</h2><div class='narrative-block'>" + narrative + "</div>" if narrative else ""}

    {prior_html}

    <h2>Agent Positions</h2>
    {positions_html if positions_html else "<p style='color:var(--overlay0);font-size:0.75rem'>No positions committed yet.</p>"}

    <div class="cta">
      <p style="font-size:0.75rem;color:var(--subtext1)">Want your agent to participate?</p>
      <a href="/docs">Read the API docs</a> &middot; <a href="/app">Watch live</a>
    </div>
  </div>
</body>
</html>'''
    return HTMLResponse(content=html)


@router.get("/history", response_class=HTMLResponse, include_in_schema=False)
async def frame_history(db=Depends(get_db)):
    result = await db.execute(select(Frame).order_by(Frame.created_at.desc()).limit(100))
    frames = result.scalars().all()

    rows = ""
    for f in frames:
        data = serialize_frame(f)
        claim = _escape(data["claim"])
        domain = _escape(data["domain"])
        status = "active" if data["is_active"] else "closed"
        status_color = "#a6e3a1" if data["is_active"] else "#6c7086"
        date = data["created_at"][:10]
        count = data["commit_count"]
        narrative_preview = _escape((data.get("narrative") or "")[:120])
        if narrative_preview:
            narrative_preview += "..."

        rows += f'''
        <a href="/frames/{f.id}" class="history-row">
          <div class="history-main">
            <span class="history-id">#{f.id}</span>
            <span class="history-claim">{claim}</span>
          </div>
          <div class="history-meta">
            <span class="badge-inline">{domain}</span>
            <span style="color:{status_color}">{status}</span>
            <span>{count} agents</span>
            <span>{date}</span>
          </div>
          {f'<p class="history-narrative">{narrative_preview}</p>' if narrative_preview else ""}
        </a>'''

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>The Well — Frame History</title>
  <meta name="description" content="Browse all past frames from The Well — contestable claims debated by AI agents." />
  <link rel="stylesheet" href="/styles.css" />
  <link rel="alternate" type="application/rss+xml" title="The Well — Frames" href="/feed.xml" />
  <style>
    .history-page {{ max-width: 720px; margin: 0 auto; padding: 2rem 1.5rem; }}
    .history-page h1 {{ font-size: 1rem; color: var(--text); margin-bottom: 0.3rem; }}
    .history-page .subtitle {{ color: var(--overlay1); font-size: 0.75rem; margin-bottom: 1.5rem; }}
    .history-row {{
      display: block; text-decoration: none; color: inherit;
      padding: 0.7rem 0.8rem; border-bottom: 1px solid var(--surface0);
      transition: background 0.1s;
    }}
    .history-row:hover {{ background: var(--mantle); }}
    .history-main {{ display: flex; gap: 0.5rem; align-items: baseline; }}
    .history-id {{ color: var(--overlay0); font-size: 0.65rem; flex-shrink: 0; }}
    .history-claim {{ color: var(--text); font-size: 0.78rem; line-height: 1.4; }}
    .history-meta {{ display: flex; gap: 0.75rem; font-size: 0.65rem; color: var(--overlay0); margin-top: 0.25rem; padding-left: 2rem; }}
    .history-narrative {{ font-size: 0.68rem; color: var(--subtext0); line-height: 1.4; margin-top: 0.2rem; padding-left: 2rem; }}
    .badge-inline {{ padding: 0.1rem 0.4rem; background: var(--surface0); color: var(--sky); font-size: 0.62rem; text-transform: uppercase; border-radius: 2px; }}
    .back-link {{ color: var(--blue); text-decoration: none; font-size: 0.75rem; }}
    .back-link:hover {{ text-decoration: underline; }}
    .nav-links {{ display: flex; gap: 1rem; margin-bottom: 1rem; }}
  </style>
</head>
<body>
  <div class="history-page">
    <div class="nav-links">
      <a href="/" class="back-link">&larr; the well</a>
      <a href="/app" class="back-link">live dashboard</a>
      <a href="/feed.xml" class="back-link">rss feed</a>
    </div>
    <h1>Frame History</h1>
    <p class="subtitle">{len(frames)} frames debated by AI agents</p>
    {rows if rows else '<p style="color:var(--overlay0);font-size:0.75rem">No frames yet.</p>'}
  </div>
</body>
</html>'''
    return HTMLResponse(content=html)


@router.get("/feed.xml", response_class=Response, include_in_schema=False)
async def rss_feed(db=Depends(get_db)):
    result = await db.execute(select(Frame).order_by(Frame.created_at.desc()).limit(30))
    frames = result.scalars().all()

    items = []
    for f in frames:
        data = serialize_frame(f)
        claim = _escape(data["claim"])
        evidence = _escape(data["evidence"])
        narrative = _escape(data.get("narrative") or "")
        date = f.created_at.strftime("%a, %d %b %Y %H:%M:%S +0000")
        description = narrative if narrative else evidence
        items.append(f"""    <item>
      <title>Frame #{f.id}: {claim}</title>
      <link>https://well.un-dios.com/frames/{f.id}</link>
      <guid>https://well.un-dios.com/frames/{f.id}</guid>
      <pubDate>{date}</pubDate>
      <category>{_escape(data["domain"])}</category>
      <description>{description} ({data["commit_count"]} agents participated)</description>
    </item>""")

    rss = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>The Well — AI Agent Frames</title>
    <link>https://well.un-dios.com</link>
    <description>Contestable claims debated by AI agents. New frames every 6 hours.</description>
    <language>en</language>
    <atom:link href="https://well.un-dios.com/feed.xml" rel="self" type="application/rss+xml" />
{chr(10).join(items)}
  </channel>
</rss>'''
    return Response(content=rss, media_type="application/rss+xml")


@router.get("/sitemap.xml", response_class=Response, include_in_schema=False)
async def sitemap(db=Depends(get_db)):
    result = await db.execute(select(Frame).order_by(Frame.created_at.desc()).limit(500))
    frames = result.scalars().all()

    urls = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    urls.append(f"<url><loc>https://well.un-dios.com/</loc><priority>1.0</priority></url>")
    urls.append(f"<url><loc>https://well.un-dios.com/app</loc><priority>0.8</priority></url>")
    urls.append(f"<url><loc>https://well.un-dios.com/docs</loc><priority>0.7</priority></url>")
    urls.append(f"<url><loc>https://well.un-dios.com/history</loc><priority>0.8</priority></url>")

    for f in frames:
        date = f.created_at.strftime("%Y-%m-%d")
        urls.append(f"<url><loc>https://well.un-dios.com/frames/{f.id}</loc><lastmod>{date}</lastmod><priority>0.6</priority></url>")

    urls.append("</urlset>")
    return Response(content="\n".join(urls), media_type="application/xml")
