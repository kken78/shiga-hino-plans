#!/usr/bin/env python3
"""町のサイトを巡回し、原典の台帳(data/sources_index.tsv)との違いを見つける。

見つけるもの(種類):
  replaced  差し替え。台帳の URL の PDF の sha256 が台帳と違う
  missing   取れない。台帳の URL が 404 などで取れない
  new       新しい PDF。計画の掲載ページ(data/manifest.json の url と、台帳の note の「(ID:nnnn)」。附表の予算のページは除く)に、
            台帳に無い PDF のリンクがある。対象外と決めたもの(data/watch_ignore.tsv の url)は除く

町のサイトへの負担を減らすため、まず HEAD で Content-Length を台帳の bytes と比べ、同じなら GET しない。
HEAD が使えない・Content-Length が無い・bytes と違うときだけ GET して sha256 を比べる。取得は1秒に1件以内。
Webアーカイブから取った原典(note が「取得元：Webアーカイブ」のもの)は、町のサイトに無いので巡回の対象から外す。

用法:
  python3 tools/watch_sources.py                      # 全部を巡回し、結果を標準出力に Markdown で出す
  python3 tools/watch_sources.py --json out.json      # 結果を JSON にも書く
  python3 tools/watch_sources.py --only head|pages    # 原典の確認だけ、掲載ページの確認だけ
  python3 tools/watch_sources.py --ledger 別の台帳.tsv  # 台帳を差し替えて試す(自己検査)
  python3 tools/watch_sources.py --limit 5 --offset 10  # 11件目から5件だけ(動作確認)
  python3 tools/watch_sources.py --full               # 全部 GET して sha256 を比べる(約 760MB。年に数回、改定の見落としを疑うとき)

HEAD だけで済ませる条件は、Content-Length が台帳の bytes と同じで、Last-Modified が台帳の fetched(取得日)より後でないこと。
同じ大きさで差し替えられ、かつ Last-Modified が取得日より前に戻されたものは、--full でないと見つからない。

終了コード:
  0  巡回できた(見つけたものの有無によらない)
  2  巡回に失敗した(掲載ページが取れない、原典の確認の2割以上が通信の失敗、台帳が読めない)
"""
import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "data", "sources_index.tsv")
MANIFEST = os.path.join(ROOT, "data", "manifest.json")
IGNORE = os.path.join(ROOT, "data", "watch_ignore.tsv")   # 対象外と決めた PDF(url・reason・date)。無ければ空
HOST = "www.town.shiga-hino.lg.jp"
UA = "hino-plans-watch/1.0 (+https://github.com/kken78/shiga-hino-plans)"
TIMEOUT = 30
INTERVAL = 1.0          # 秒。1秒に1件以内
JST = timezone(timedelta(hours=9))

_last = [0.0]


def _wait():
    now = time.monotonic()
    rest = INTERVAL - (now - _last[0])
    if rest > 0:
        time.sleep(rest)
    _last[0] = time.monotonic()


def _request(url, method="GET"):
    """(status, headers, body) を返す。HTTP の誤りは status に入れる。通信の失敗は例外のまま。"""
    _wait()
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = b"" if method == "HEAD" else r.read()
            return r.status, dict(r.headers), body
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), b""


def load_ledger(path):
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    need = {"id", "file", "url", "bytes", "sha256", "note"}
    if rows and not need <= set(rows[0]):
        raise SystemExit(f"台帳の列が足りない: {sorted(need - set(rows[0]))}")
    return rows


def targets(rows):
    """巡回する行(URL と sha256 があり、Webアーカイブから取ったものでない)。"""
    out = []
    for r in rows:
        if not r["url"] or not r["sha256"]:
            continue
        if "取得元：Webアーカイブ" in r["note"] or "取得元:Webアーカイブ" in r["note"]:
            continue
        out.append(r)
    return out


def _newer_than_fetched(last_modified, fetched):
    """HEAD の Last-Modified が、台帳の取得日(fetched。YYYY-MM-DD)より後の日付か。どちらかが無ければ True(GET して確かめる)。"""
    if not last_modified or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fetched or ""):
        return True
    try:
        lm = parsedate_to_datetime(last_modified).astimezone(JST).date()
    except (TypeError, ValueError):
        return True
    return lm.isoformat() > fetched


def check_sources(rows, limit=None, offset=0, full=False):
    """原典の確認。結果の一覧と、通信の失敗の数を返す。

    HEAD の Content-Length が台帳の bytes と同じで、Last-Modified が台帳の fetched より後でなければ、変更なしとみなして GET しない。
    それ以外(長さが違う・無い、Last-Modified が後、fetched が無い、HEAD が使えない)は GET して sha256 を比べる。
    full=True のときは全部 GET する(台帳の全件で約 760MB)。"""
    results = []
    net_fail = 0
    for r in rows[offset: offset + limit] if limit else rows[offset:]:
        url = r["url"]
        res = {"id": r["id"], "file": r["file"], "url": url, "bytes": int(r["bytes"] or 0),
               "sha256": r["sha256"], "kind": "same", "how": "", "status": None, "length": None,
               "last_modified": "", "now_sha256": "", "now_bytes": None, "reason": ""}
        try:
            st, hd, _ = _request(url, "HEAD")
            res["status"] = st
            res["last_modified"] = hd.get("Last-Modified", "")
            cl = hd.get("Content-Length")
            res["length"] = int(cl) if cl and cl.isdigit() else None
            if st in (404, 410):
                res.update(kind="missing", how="HEAD", reason=f"HTTP {st}")
            elif (not full and st == 200 and res["length"] is not None and res["length"] == res["bytes"]
                  and not _newer_than_fetched(res["last_modified"], r.get("fetched", ""))):
                res.update(kind="same", how="HEAD")
            else:
                # HEAD が使えない、長さが無い・違う、Last-Modified が取得日より後、--full → GET して sha256 を比べる
                st2, hd2, body = _request(url, "GET")
                res["status"] = st2
                if st2 in (404, 410):
                    res.update(kind="missing", how="GET", reason=f"HTTP {st2}")
                elif st2 != 200:
                    res.update(kind="error", how="GET", reason=f"HTTP {st2}")
                    net_fail += 1
                else:
                    sha = hashlib.sha256(body).hexdigest()
                    res.update(now_sha256=sha, now_bytes=len(body), how="GET",
                               last_modified=hd2.get("Last-Modified", res["last_modified"]))
                    if sha == r["sha256"]:
                        res["kind"] = "same"
                        if res["length"] is not None and res["length"] != res["bytes"]:
                            res["reason"] = f"HEAD の Content-Length {res['length']} は bytes と違うが、GET の sha256 は一致"
                    else:
                        res.update(kind="replaced", reason="sha256 が台帳と違う")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            res.update(kind="error", reason=f"通信の失敗: {e}")
            net_fail += 1
        results.append(res)
    return results, net_fail


def page_urls(manifest_path, rows):
    """計画の掲載ページ(manifest の url)と、台帳の note の「(ID:nnnn)」のページ。{url: 由来}"""
    pages = {}
    with open(manifest_path, encoding="utf-8") as f:
        m = json.load(f)
    for p in m.get("plans", []):
        u = p.get("url", "")
        if u.startswith("http"):
            pages.setdefault(u, []).append(p.get("id", ""))
    for r in rows:
        if r["id"].startswith("fuhyo-"):
            # 当初予算の附表(PLAN_SCHEMA §9)の掲載ページ(「令和○年度予算」)には補正予算の概要など、この台帳の対象でない
            # PDF が多数ある。新しい年度の附表は新しいページに載るので、予算のページの巡回は hino-budget の側で行う
            continue
        for mid in re.findall(r"\(ID:(\d+)\)", r["note"]):
            u = f"https://{HOST}/{int(mid):010d}.html"
            pages.setdefault(u, []).append(r["id"])
    return pages


HREF_RE = re.compile(r"""<a\b[^>]*?href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""", re.I | re.S)


def pdf_links(base, body):
    """ページの中の PDF へのリンク [(絶対URL, リンクの文字)]。町のサイトのものだけ。"""
    text = body.decode("utf-8", errors="replace")
    out = []
    seen = set()
    for href, inner in HREF_RE.findall(text):
        href = html.unescape(href.strip())
        absu = urllib.parse.urljoin(base, href)
        pu = urllib.parse.urlsplit(absu)
        if pu.netloc != HOST or not pu.path.lower().endswith(".pdf"):
            continue
        absu = urllib.parse.urlunsplit((pu.scheme, pu.netloc, pu.path, "", ""))
        if absu in seen:
            continue
        seen.add(absu)
        label = re.sub(r"<[^>]+>", "", inner)
        label = html.unescape(re.sub(r"\s+", " ", label)).strip()
        # 町のサイトがリンクの文字に添える「サイズ：8.64MB」「サイズ325.60KB」「(PDF形式、230.48KB)」を除く
        label = re.sub(r"\s*(サイズ|ファイルサイズ)[：:]?\s*[\d.,]+\s*[KMG]?B.*$", "", label).strip()
        label = re.sub(r"\s*[（(]PDF形式[、,][^）)]*[）)]\s*$", "", label).strip()
        out.append((absu, label))
    return out


def norm(u):
    pu = urllib.parse.urlsplit(u.strip())
    return urllib.parse.urlunsplit((pu.scheme, pu.netloc, pu.path, "", ""))


def load_ignore(path):
    """対象外と決めた PDF の URL の集合。列は url / reason / date。"""
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    if rows and "url" not in rows[0]:
        raise SystemExit(f"{path} に url の列が無い")
    return {norm(r["url"]) for r in rows if r.get("url")}


def check_pages(pages, rows, limit=None, offset=0, ignore=frozenset()):
    """掲載ページの確認。台帳に無い PDF(対象外と決めたものを除く)と、取れないページを返す。"""
    known = {norm(r["url"]) for r in rows if r["url"]} | set(ignore)
    new, failed = [], []
    items = list(pages.items())
    for url, ids in items[offset: offset + limit] if limit else items[offset:]:
        try:
            st, _, body = _request(url, "GET")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            failed.append({"url": url, "ids": ids, "reason": f"通信の失敗: {e}"})
            continue
        if st != 200:
            failed.append({"url": url, "ids": ids, "reason": f"HTTP {st}"})
            continue
        for pdf, label in pdf_links(url, body):
            if pdf not in known:
                new.append({"url": pdf, "label": label, "page": url, "ids": ids})
    # 同じ PDF が複数のページから見つかったら1つにまとめる
    merged = {}
    for n in new:
        m = merged.setdefault(n["url"], {"url": n["url"], "label": n["label"], "pages": [], "ids": []})
        if n["page"] not in m["pages"]:
            m["pages"].append(n["page"])
        for i in n["ids"]:
            if i not in m["ids"]:
                m["ids"].append(i)
    return list(merged.values()), failed


def summary_md(rep):
    L = []
    f = rep["findings"]
    L.append(f"町のサイトを {rep['date']} に巡回した結果。原典 {rep['checked_sources']} 件(HEAD だけ {rep['head_only']}・GET {rep['got']})、掲載ページ {rep['checked_pages']} 件。")
    L.append("")
    L.append(f"## 差し替え({len(f['replaced'])}件)")
    for x in f["replaced"] or []:
        L.append(f"- {x['id']}/{x['file']}: 台帳 {x['bytes']} バイト・{x['sha256'][:12]}… → いま {x['now_bytes']} バイト・{x['now_sha256'][:12]}…(Last-Modified {x['last_modified'] or '不明'})。{x['url']}")
    if not f["replaced"]:
        L.append("- なし")
    L.append("")
    L.append(f"## 取れない({len(f['missing'])}件)")
    for x in f["missing"] or []:
        L.append(f"- {x['id']}/{x['file']}: {x['reason']}。{x['url']}")
    if not f["missing"]:
        L.append("- なし")
    L.append("")
    L.append(f"## 新しい PDF({len(f['new'])}件)")
    for x in f["new"] or []:
        L.append(f"- {x['label'] or '(文字なし)'}: {x['url']}(掲載ページ {', '.join(x['pages'])}。関係する id: {', '.join(x['ids']) or '不明'})")
    if not f["new"]:
        L.append("- なし")
    if rep["errors"]:
        L.append("")
        L.append(f"## 確かめられなかったもの({len(rep['errors'])}件)")
        for x in rep["errors"]:
            L.append(f"- {x.get('id', '')}{'/' + x['file'] if x.get('file') else ''} {x.get('url', '')}: {x['reason']}")
    if rep["page_failures"]:
        L.append("")
        L.append(f"## 取れない掲載ページ({len(rep['page_failures'])}件)")
        for x in rep["page_failures"]:
            L.append(f"- {x['url']}({', '.join(x['ids'])}): {x['reason']}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", default=LEDGER)
    ap.add_argument("--manifest", default=MANIFEST)
    ap.add_argument("--ignore", default=IGNORE, help="対象外と決めた PDF の一覧(url・reason・date)")
    ap.add_argument("--json", help="結果を書く JSON")
    ap.add_argument("--only", choices=["head", "pages"])
    ap.add_argument("--full", action="store_true", help="HEAD で済ませず全部 GET する(約 760MB。改定の見落としを疑うときだけ)")
    ap.add_argument("--limit", type=int, help="N 件だけ(動作確認)")
    ap.add_argument("--offset", type=int, default=0, help="--limit と合わせて、何件目から(動作確認)")
    a = ap.parse_args()

    rows = load_ledger(a.ledger)
    tg = targets(rows)
    rep = {"date": datetime.now(JST).strftime("%Y-%m-%d %H:%M JST"), "ledger_rows": len(rows),
           "checked_sources": 0, "head_only": 0, "got": 0, "checked_pages": 0,
           "findings": {"replaced": [], "missing": [], "new": []}, "errors": [], "page_failures": [],
           "failed": False, "fail_reason": ""}

    if a.only != "pages":
        results, net_fail = check_sources(tg, a.limit, a.offset, a.full)
        rep["checked_sources"] = len(results)
        rep["head_only"] = sum(1 for r in results if r["how"] == "HEAD" and r["kind"] == "same")
        rep["got"] = sum(1 for r in results if r["how"] == "GET")
        rep["findings"]["replaced"] = [r for r in results if r["kind"] == "replaced"]
        rep["findings"]["missing"] = [r for r in results if r["kind"] == "missing"]
        rep["errors"] = [r for r in results if r["kind"] == "error"]
        if results and net_fail * 5 >= len(results):
            rep["failed"] = True
            rep["fail_reason"] = f"原典の確認 {len(results)} 件のうち {net_fail} 件が通信の失敗"

    if a.only != "head":
        pages = page_urls(a.manifest, rows)
        new, failed = check_pages(pages, rows, a.limit, a.offset, load_ignore(a.ignore))
        rep["checked_pages"] = min(len(pages) - a.offset, a.limit) if a.limit else len(pages)
        rep["findings"]["new"] = new
        rep["page_failures"] = failed
        if failed:
            rep["failed"] = True
            rep["fail_reason"] = (rep["fail_reason"] + "。" if rep["fail_reason"] else "") + f"掲載ページ {len(failed)} 件が取れない"

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
    print(summary_md(rep))
    if rep["failed"]:
        print(f"\n巡回に失敗した: {rep['fail_reason']}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
