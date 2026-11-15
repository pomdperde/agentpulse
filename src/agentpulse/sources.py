from __future__ import annotations

import asyncio
import html
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from agentpulse.config import Settings
from agentpulse.http import HttpClient, HttpError
from agentpulse.models import Paper
from agentpulse.venues import pick_source, venue_from_comment, venue_patterns

ARXIV_API = "https://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENREVIEW_API = "https://api2.openreview.net/notes/search"
OPENALEX_API = "https://api.openalex.org/works"
OPENALEX_SOURCES_API = "https://api.openalex.org/sources"
_OPENALEX_SELECT = (
    "id,doi,title,display_name,publication_date,created_date,abstract_inverted_index,"
    "authorships,cited_by_count,primary_location,type,primary_topic"
)
CROSSREF_API = "https://api.crossref.org/works"
EUROPEPMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
NCBI_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
HN_API = "https://hn.algolia.com/api/v1/search_by_date"
DOAJ_API = "https://doaj.org/api/search/articles"
BIORXIV_API = "https://api.biorxiv.org/details"
HAL_API = "https://api.archives-ouvertes.fr/search/"
ZENODO_API = "https://zenodo.org/api/records"

_ATOM = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


def _arxiv_query(query: str) -> str:
    """arXiv 需要字段前缀；裸关键词补成 all:"..."。"""
    if ":" in query:
        return query
    return f'all:"{query.strip().strip(chr(34))}"'  # chr(34) 即双引号


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _pick_published(*values: date | None, today: date | None = None) -> date | None:
    """优先取不晚于今天的日期（在线/电子发表优先）；全部为未来期次时取最早值。"""
    today = today or date.today()
    dates = [value for value in values if value]
    past = [value for value in dates if value <= today]
    if past:
        return min(past)
    return min(dates) if dates else None


async def search_arxiv(http: HttpClient, query: str, limit: int, cutoff: date) -> list[Paper]:
    params = {
        "search_query": _arxiv_query(query),
        "start": 0,
        "max_results": limit,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    text = await http.get_text(ARXIV_API, params=params)
    root = ET.fromstring(text)

    papers: list[Paper] = []
    for entry in root.findall("a:entry", _ATOM):
        raw_id = _clean(entry.findtext("a:id", default="", namespaces=_ATOM))
        match = re.search(r"abs/([0-9.]+)(?:v\d+)?", raw_id)
        if not match:
            continue
        arxiv_id = match.group(1)
        published = _parse_date(entry.findtext("a:published", default="", namespaces=_ATOM))
        if published and published < cutoff:
            continue
        authors = [
            _clean(name.text)
            for name in entry.findall("a:author/a:name", _ATOM)
            if name.text
        ]
        categories = [
            term
            for node in entry.findall("a:category", _ATOM)
            if (term := node.get("term"))
        ]
        papers.append(
            Paper(
                uid=f"arxiv:{arxiv_id}",
                source="arxiv",
                title=_clean(entry.findtext("a:title", default="", namespaces=_ATOM)),
                abstract=_clean(entry.findtext("a:summary", default="", namespaces=_ATOM)),
                authors=authors,
                published=published,
                url=f"https://arxiv.org/abs/{arxiv_id}",
                venue=(
                    _clean(entry.findtext("arxiv:journal_ref", default="", namespaces=_ATOM))
                    or venue_from_comment(
                        _clean(entry.findtext("arxiv:comment", default="", namespaces=_ATOM))
                    )
                    or "arXiv"
                ),
                categories=categories,
            )
        )
    return papers


async def search_semantic_scholar(
    http: HttpClient, query: str, limit: int, cutoff: date, api_key: str = ""
) -> list[Paper]:
    params = {
        "query": query,
        "limit": limit,
        "fields": "paperId,title,abstract,authors,venue,citationCount,url,publishedDate,fieldsOfStudy",
    }
    headers = {"x-api-key": api_key} if api_key else {}
    data = await http.get_json(S2_API, params=params, headers=headers)

    papers: list[Paper] = []
    for item in data.get("data", []) or []:
        published = _parse_date(item.get("publishedDate"))
        if published and published < cutoff:
            continue
        papers.append(
            Paper(
                uid=f"ss:{item.get('paperId', '')}",
                source="semantic_scholar",
                title=_clean(item.get("title")),
                abstract=_clean(item.get("abstract")),
                authors=[_clean(a.get("name")) for a in item.get("authors", []) if a.get("name")],
                published=published,
                url=item.get("url") or "",
                venue=_clean(item.get("venue")),
                categories=[str(c) for c in (item.get("fieldsOfStudy") or []) if c],
                citation_count=int(item.get("citationCount") or 0),
            )
        )
    return papers


def _content_value(content: dict, key: str) -> object:
    """OpenReview v2 的 content 是 {key: {value: ...}}，v1 是裸值。"""
    raw = content.get(key)
    if isinstance(raw, dict):
        return raw.get("value")
    return raw


def _openreview_date(note: dict) -> date | None:
    # cdate 是投稿时间，pdate 可能是会议/出版日期（常晚于当前），
    # 热点检索用投稿时间更合理，也避免未来日期混进时间窗口。
    ts = note.get("cdate") or note.get("pdate") or note.get("tcdate")
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).date()
    except (ValueError, OSError, OverflowError):
        return None


def parse_openreview_notes(payload: dict, cutoff: date) -> list[Paper]:
    """把 OpenReview 的 notes 响应归一成 Paper。

    只保留 forum 本体（id == forum），过滤评审与评论；
    标题缺失的条目直接丢弃，避免把讨论串当论文。
    """
    papers: list[Paper] = []
    for note in payload.get("notes", []) or []:
        content = note.get("content") or {}
        forum = note.get("forum") or note.get("id")
        if not forum or note.get("id") != forum:
            continue
        title = _clean(str(_content_value(content, "title") or ""))
        if not title:
            continue
        published = _openreview_date(note)
        if published and published < cutoff:
            continue
        raw_authors = _content_value(content, "authors") or []
        if isinstance(raw_authors, str):
            raw_authors = [raw_authors]
        venue = _content_value(content, "venue") or _content_value(content, "venueid")
        papers.append(
            Paper(
                uid=f"openreview:{forum}",
                source="openreview",
                title=title,
                abstract=_clean(str(_content_value(content, "abstract") or "")),
                authors=[_clean(str(a)) for a in raw_authors if a],
                published=published,
                url=f"https://openreview.net/forum?id={forum}",
                venue=_clean(str(venue or "OpenReview")) or "OpenReview",
            )
        )
    return papers


async def search_openreview(http: HttpClient, query: str, limit: int, cutoff: date) -> list[Paper]:
    params = {
        "term": query,
        "type": "terms",
        "content": "all",
        "group": "all",
        "source": "forum",
        "limit": limit,
        "offset": 0,
    }
    data = await http.get_json(OPENREVIEW_API, params=params)
    return parse_openreview_notes(data, cutoff)


def _strip_tags(text: str | None) -> str:
    """Crossref/Europe PMC 的摘要常带 JATS/HTML 标签，先剥标签再反转义。"""
    if not text:
        return ""
    plain = _clean(html.unescape(re.sub(r"<[^>]+>", " ", text)))
    # 去标签会在标点前留下空格，如 "benefit ."
    return re.sub(r"\s+([,.;:!?])", r"\1", plain)


def _openalex_abstract(inverted: dict | None) -> str:
    """OpenAlex 用倒排索引存摘要，按位置还原成正文。"""
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, indexes in inverted.items():
        for index in indexes or []:
            positions.append((int(index), word))
    positions.sort()
    return _clean(" ".join(word for _, word in positions))


def _crossref_date(item: dict) -> date | None:
    def extract(block: dict | None) -> date | None:
        parts = (block or {}).get("date-parts") or []
        if not parts or not parts[0]:
            return None
        values = list(parts[0]) + [1, 1]
        try:
            return date(int(values[0]), int(values[1]), int(values[2]))
        except (TypeError, ValueError):
            return None

    dates = [
        extract(item.get(key))
        for key in ("published-online", "published-print", "issued", "published")
    ]
    created = extract(item.get("created"))
    picked = _pick_published(*dates)
    # 期刊“出版日期”可能是未来期次；此时退回 DOI 注册时间，避免未来文献占据榜单
    if picked and picked > date.today() and created and created <= date.today():
        return created
    return picked or created


def parse_openalex_works(payload: dict, cutoff: date) -> list[Paper]:
    papers: list[Paper] = []
    for item in payload.get("results") or []:
        title = _clean(str(item.get("title") or item.get("display_name") or ""))
        if not title:
            continue
        published = _parse_date(item.get("publication_date"))
        if published is None or published > date.today():
            created = _parse_date(item.get("created_date"))
            if created and created <= date.today():
                published = created
        if published and published < cutoff:
            continue
        location = item.get("primary_location") or {}
        source = location.get("source") or {}
        field = ((item.get("primary_topic") or {}).get("field") or {}).get("display_name")
        raw_id = str(item.get("id") or "")
        doi = str(item.get("doi") or "")
        authors = [
            _clean(str((authorship.get("author") or {}).get("display_name") or ""))
            for authorship in item.get("authorships") or []
        ]
        papers.append(
            Paper(
                uid=f"openalex:{raw_id.rsplit('/', 1)[-1] or title[:40]}",
                source="openalex",
                title=title,
                abstract=_openalex_abstract(item.get("abstract_inverted_index")),
                authors=[a for a in authors if a],
                published=published,
                url=doi or str(location.get("landing_page_url") or raw_id),
                venue=_clean(str(source.get("display_name") or "OpenAlex")),
                categories=[_clean(field)] if field else [],
                citation_count=int(item.get("cited_by_count") or 0),
            )
        )
    return papers


async def search_openalex(
    http: HttpClient, query: str, limit: int, cutoff: date, mailto: str = ""
) -> list[Paper]:
    params = {
        "search": query,
        "per_page": limit,
        "filter": f"from_publication_date:{cutoff.isoformat()}",
        "select": _OPENALEX_SELECT,
    }
    if mailto:
        params["mailto"] = mailto
    try:
        data = await http.get_json(OPENALEX_API, params=params)
    except HttpError as exc:
        # select 字段不被接受时退回完整响应，保证源可用
        if exc.status != 400:
            raise
        params.pop("select", None)
        data = await http.get_json(OPENALEX_API, params=params)
    return parse_openalex_works(data, cutoff)


_source_cache: dict[str, tuple[str, str] | None] = {}


async def resolve_openalex_source(
    http: HttpClient, venue: str, mailto: str = ""
) -> tuple[str, str] | None:
    """把会议/期刊名解析成 OpenAlex 的 source id，进程内缓存。"""
    key = venue.strip().lower()
    if key in _source_cache:
        return _source_cache[key]

    # 先按原词搜，再退到全称；NIPS 这类旧称常只能搜到零论文的 workshop
    attempts = [venue] + [
        pattern
        for pattern in sorted(venue_patterns(venue), key=len, reverse=True)
        if pattern != key
    ]
    for term in dict.fromkeys(attempts):
        params: dict[str, str | int] = {"search": term, "per-page": 10}
        if mailto:
            params["mailto"] = mailto
        try:
            data = await http.get_json(OPENALEX_SOURCES_API, params=params)
        except Exception:
            continue

        picked = pick_source(data.get("results") or [], venue)
        if not picked:
            continue
        raw = str(picked.get("id") or "")
        source_id = raw.rsplit("/", 1)[-1]
        if not source_id:
            continue
        resolved = (source_id, _clean(str(picked.get("display_name") or venue)))
        _source_cache[key] = resolved
        return resolved

    _source_cache[key] = None
    return None


async def search_openalex_venue(
    http: HttpClient,
    query: str,
    venue: str,
    limit: int,
    cutoff: date,
    mailto: str = "",
) -> tuple[list[Paper], str]:
    """直接抓取指定会议/期刊在 OpenAlex 的来源库，返回论文与命中的来源名。"""
    resolved = await resolve_openalex_source(http, venue, mailto)
    if resolved is None:
        return [], ""
    source_id, display_name = resolved

    params: dict[str, str | int] = {
        "filter": (
            f"primary_location.source.id:{source_id},"
            f"from_publication_date:{cutoff.isoformat()}"
        ),
        "search": query,
        "per_page": max(1, min(limit, 100)),
        "sort": "publication_date:desc",
        "select": _OPENALEX_SELECT,
    }
    if mailto:
        params["mailto"] = mailto
    try:
        data = await http.get_json(OPENALEX_API, params=params)
    except HttpError as exc:
        if exc.status != 400:
            raise
        params.pop("select", None)
        data = await http.get_json(OPENALEX_API, params=params)
    papers = parse_openalex_works(data, cutoff)
    return papers, display_name


def parse_crossref_items(payload: dict, cutoff: date) -> list[Paper]:
    papers: list[Paper] = []
    for item in ((payload.get("message") or {}).get("items")) or []:
        titles = item.get("title") or []
        title = _clean(titles[0]) if titles else ""
        if not title:
            continue
        published = _crossref_date(item)
        if published and published < cutoff:
            continue
        doi = str(item.get("DOI") or "")
        authors: list[str] = []
        for author in item.get("author") or []:
            name = _clean(" ".join(filter(None, [author.get("given"), author.get("family")])))
            name = name or _clean(author.get("name"))
            if name:
                authors.append(name)
        containers = item.get("container-title") or []
        papers.append(
            Paper(
                uid=f"crossref:{doi or title[:40]}",
                source="crossref",
                title=title,
                abstract=_strip_tags(item.get("abstract")),
                authors=authors,
                published=published,
                url=str(item.get("URL") or (f"https://doi.org/{doi}" if doi else "")),
                venue=_clean(containers[0] if containers else "") or _clean(item.get("publisher")),
                categories=[str(s) for s in item.get("subject") or []],
                citation_count=int(item.get("is-referenced-by-count") or 0),
            )
        )
    return papers


async def search_crossref(
    http: HttpClient, query: str, limit: int, cutoff: date, mailto: str = ""
) -> list[Paper]:
    params = {
        "query": query,
        "rows": limit,
        "filter": f"from-pub-date:{cutoff.isoformat()}",
    }
    if mailto:
        params["mailto"] = mailto
    data = await http.get_json(CROSSREF_API, params=params)
    return parse_crossref_items(data, cutoff)


def parse_europepmc(payload: dict, cutoff: date) -> list[Paper]:
    papers: list[Paper] = []
    results = ((payload.get("resultList") or {}).get("result")) or []
    for item in results:
        title = _clean(item.get("title"))
        if not title:
            continue
        published = _pick_published(
            _parse_date(item.get("firstPublicationDate")),
            _parse_date(item.get("electronicPublicationDate")),
            _parse_date(item.get("firstIndexDate")),
        )
        if published and published < cutoff:
            continue
        doi = _clean(item.get("doi"))
        source_code = _clean(item.get("source")) or "MED"
        record_id = _clean(str(item.get("id") or ""))
        journal = ((item.get("journalInfo") or {}).get("journal") or {}).get("title")
        authors = [a.strip() for a in (item.get("authorString") or "").split(",") if a.strip()]
        papers.append(
            Paper(
                uid=f"europepmc:{source_code}:{record_id or doi}",
                source="europepmc",
                title=title,
                abstract=_strip_tags(item.get("abstractText")),
                authors=authors,
                published=published,
                url=f"https://doi.org/{doi}" if doi else f"https://europepmc.org/article/{source_code}/{record_id}",
                venue=_clean(journal) or "Europe PMC",
                citation_count=int(item.get("citedByCount") or 0),
            )
        )
    return papers


async def search_europepmc(http: HttpClient, query: str, limit: int, cutoff: date) -> list[Paper]:
    params = {
        "query": f"({query}) AND (FIRST_PDATE:[{cutoff.isoformat()} TO 3000-12-31])",
        "format": "json",
        "pageSize": limit,
        "resultType": "core",
    }
    data = await http.get_json(EUROPEPMC_API, params=params)
    return parse_europepmc(data, cutoff)


_PUBMED_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _pubmed_date(node: ET.Element | None) -> date | None:
    if node is None:
        return None
    year_text = (node.findtext("Year") or "").strip()
    if not year_text:
        match = re.search(r"\b(19|20)\d{2}\b", node.findtext("MedlineDate") or "")
        year_text = match.group(0) if match else ""
    if not year_text.isdigit():
        return None
    month_text = (node.findtext("Month") or "1").strip()
    if month_text.isdigit():
        month = int(month_text)
    else:
        month = _PUBMED_MONTHS.get(month_text[:3].lower(), 1)
    day_text = (node.findtext("Day") or "1").strip()
    day = int(day_text) if day_text.isdigit() else 1
    try:
        return date(int(year_text), month, day)
    except ValueError:
        return None


def parse_pubmed_xml(xml_text: str | bytes, cutoff: date) -> list[Paper]:
    root = ET.fromstring(xml_text)
    papers: list[Paper] = []
    for record in root.findall(".//PubmedArticle"):
        citation = record.find("MedlineCitation")
        article = citation.find("Article") if citation is not None else None
        if citation is None or article is None:
            continue
        pmid = (citation.findtext("PMID") or "").strip()
        title = _clean("".join(article.find("ArticleTitle").itertext())) if article.find("ArticleTitle") is not None else ""
        if not title:
            continue
        published = _pick_published(
            _pubmed_date(article.find("ArticleDate")),
            _pubmed_date(article.find("Journal/JournalIssue/PubDate")),
        )
        if published and published < cutoff:
            continue
        abstracts = [
            _clean("".join(node.itertext()))
            for node in article.findall("Abstract/AbstractText")
            if "".join(node.itertext()).strip()
        ]
        authors: list[str] = []
        for author in article.findall("AuthorList/Author"):
            collective = author.findtext("CollectiveName")
            name = " ".join(
                part for part in [author.findtext("ForeName"), author.findtext("LastName")] if part
            ).strip()
            name = _clean(name or collective or "")
            if name:
                authors.append(name)
        doi = ""
        for node in record.findall(".//ArticleIdList/ArticleId"):
            if node.get("IdType") == "doi" and node.text:
                doi = node.text.strip()
                break
        if not doi:
            for node in article.findall("ELocationID"):
                if node.get("EIdType") == "doi" and node.text:
                    doi = node.text.strip()
                    break
        papers.append(
            Paper(
                uid=f"pubmed:{pmid}",
                source="pubmed",
                title=title,
                abstract=" ".join(abstracts),
                authors=authors,
                published=published,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                venue=_clean(article.findtext("Journal/Title")) or "PubMed",
            )
        )
    return papers


async def search_pubmed(
    http: HttpClient,
    query: str,
    limit: int,
    cutoff: date,
    api_key: str = "",
    email: str = "",
) -> list[Paper]:
    base = {"db": "pubmed", "term": query, "tool": "agentpulse"}
    if email:
        base["email"] = email
    if api_key:
        base["api_key"] = api_key
    search_params = {
        **base,
        "retmode": "json",
        "retmax": limit,
        "sort": "date",
        "datetype": "pdat",
        "mindate": cutoff.strftime("%Y/%m/%d"),
        "maxdate": date.today().strftime("%Y/%m/%d"),
    }
    data = await http.get_json(NCBI_ESEARCH, params=search_params)
    ids = ((data.get("esearchresult") or {}).get("idlist")) or []
    if not ids:
        return []
    fetch_params = {**base, "id": ",".join(ids), "retmode": "xml", "rettype": "abstract"}
    xml_text = await http.get_text(NCBI_EFETCH, params=fetch_params)
    return parse_pubmed_xml(xml_text, cutoff)


async def search_hackernews(http: HttpClient, query: str, limit: int, cutoff: date) -> list[Paper]:
    ts = int(datetime.combine(cutoff, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    params = {
        "query": query,
        "tags": "story",
        "numericFilters": f"created_at_i>{ts}",
        "hitsPerPage": limit,
    }
    data = await http.get_json(HN_API, params=params)

    papers: list[Paper] = []
    for hit in data.get("hits", []) or []:
        title = _clean(hit.get("title"))
        if not title:
            continue
        created = hit.get("created_at_i")
        published = (
            datetime.fromtimestamp(int(created), tz=timezone.utc).date() if created else None
        )
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        papers.append(
            Paper(
                uid=f"hn:{hit.get('objectID')}",
                source="hackernews",
                title=title,
                abstract="",
                published=published,
                url=url,
                venue="Hacker News",
                heat=float(hit.get("points") or 0),
            )
        )
    return papers


def _doaj_date(bib: dict) -> date | None:
    year = _clean(str(bib.get("year") or ""))
    if not year.isdigit():
        return None
    month_raw = _clean(str(bib.get("month") or ""))
    if month_raw.isdigit():
        month_num = int(month_raw)
    else:
        month_num = _PUBMED_MONTHS.get(month_raw[:3].lower(), 1)
    return date(int(year), min(max(month_num, 1), 12), 1)


def parse_doaj(payload: dict, cutoff: date) -> list[Paper]:
    """DOAJ（开放获取期刊目录）全学科检索结果。"""
    papers: list[Paper] = []
    for item in payload.get("results") or []:
        bib = item.get("bibjson") or {}
        title = _clean(bib.get("title"))
        if not title:
            continue
        published = _doaj_date(bib)
        if published and published < cutoff:
            continue
        doi = ""
        for ident in bib.get("identifier") or []:
            if str(ident.get("type") or "").lower() == "doi":
                doi = _clean(ident.get("id"))
                break
        authors = [
            _clean(author.get("name"))
            for author in bib.get("author") or []
            if _clean(author.get("name"))
        ]
        journal = (bib.get("journal") or {}).get("title")
        links = bib.get("link") or []
        url = _clean(links[0].get("url")) if links else ""
        record_id = _clean(str(item.get("id") or ""))
        papers.append(
            Paper(
                uid=f"doaj:{record_id or doi or title[:40]}",
                source="doaj",
                title=title,
                abstract=_strip_tags(bib.get("abstract")),
                authors=authors,
                published=published,
                url=url or (f"https://doi.org/{doi}" if doi else f"https://doaj.org/article/{record_id}"),
                venue=_clean(journal) or "DOAJ",
            )
        )
    return papers


async def search_doaj(http: HttpClient, query: str, limit: int, cutoff: date) -> list[Paper]:
    data = await http.get_json(
        f"{DOAJ_API}/{quote(query, safe='')}", params={"pageSize": limit}
    )
    return parse_doaj(data, cutoff)


def parse_biorxiv(payload: dict, cutoff: date, query: str = "") -> list[Paper]:
    """bioRxiv / medRxiv 预印本；接口无关键词检索，按标题摘要本地过滤。"""
    tokens = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) > 3]
    papers: list[Paper] = []
    for item in payload.get("collection") or []:
        title = _clean(item.get("title"))
        if not title:
            continue
        abstract = _strip_tags(item.get("abstract"))
        if tokens:
            text = f"{title} {abstract}".lower()
            if not any(token in text for token in tokens):
                continue
        published = _parse_date(item.get("date"))
        if published and published < cutoff:
            continue
        doi = _clean(item.get("doi"))
        server = _clean(item.get("server")) or "biorxiv"
        authors = [a.strip() for a in (item.get("authors") or "").split(";") if a.strip()]
        papers.append(
            Paper(
                uid=f"biorxiv:{doi or title[:40]}",
                source="biorxiv",
                title=title,
                abstract=abstract,
                authors=authors,
                published=published,
                url=f"https://doi.org/{doi}" if doi else "",
                venue=f"{server} preprint",
                categories=[_clean(item.get("category"))] if item.get("category") else [],
            )
        )
    return papers


async def search_biorxiv(http: HttpClient, query: str, limit: int, cutoff: date) -> list[Paper]:
    end = date.today()
    start = max(cutoff, end - timedelta(days=30))
    papers: list[Paper] = []
    for server in ("biorxiv", "medrxiv"):
        data = await http.get_json(f"{BIORXIV_API}/{server}/{start}/{end}")
        papers.extend(parse_biorxiv(data, cutoff, query))
    return papers[:limit]


def _first(value: object) -> str:
    if isinstance(value, list):
        return _clean(str(value[0])) if value else ""
    return _clean(str(value)) if value else ""


def parse_hal(payload: dict, cutoff: date) -> list[Paper]:
    """HAL 开放档案（法语区，覆盖人文社科与自然科学）。"""
    papers: list[Paper] = []
    docs = ((payload.get("response") or {}).get("docs")) or []
    for item in docs:
        title = _first(item.get("title_s"))
        if not title:
            continue
        year = _clean(str(item.get("publicationDateY_i") or item.get("producedDateY_i") or ""))
        published = _parse_date(_clean(str(item.get("publicationDate_s") or "")))
        if published is None or published > date.today():
            for key in ("producedDate_s", "submittedDate_s"):
                fallback = _parse_date(_clean(str(item.get(key) or "")))
                if fallback and fallback <= date.today():
                    published = fallback
                    break
        if published is None and year.isdigit():
            published = date(int(year), 1, 1)
        if published and published < cutoff:
            continue
        doi = _clean(str(item.get("doiId_s") or ""))
        doc_id = _clean(str(item.get("docid") or ""))
        papers.append(
            Paper(
                uid=f"hal:{doc_id or doi or title[:40]}",
                source="hal",
                title=title,
                abstract=_first(item.get("abstract_s")),
                authors=[a for a in item.get("authFullName_s") or [] if a],
                published=published,
                url=f"https://doi.org/{doi}" if doi else _clean(str(item.get("uri_s") or "")),
                venue=_first(item.get("journalTitle_s")) or "HAL",
            )
        )
    return papers


async def search_hal(http: HttpClient, query: str, limit: int, cutoff: date) -> list[Paper]:
    params = {
        "q": query,
        "rows": limit,
        "wt": "json",
        "fl": "docid,title_s,abstract_s,authFullName_s,publicationDate_s,producedDate_s,submittedDate_s,publicationDateY_i,producedDateY_i,doiId_s,uri_s,journalTitle_s",
        "fq": f"publicationDateY_i:[{cutoff.year} TO *]",
    }
    data = await http.get_json(HAL_API, params=params)
    return parse_hal(data, cutoff)


def parse_zenodo(payload: dict, cutoff: date) -> list[Paper]:
    """Zenodo 开放仓储（论文、预印本、数据集）。"""
    papers: list[Paper] = []
    hits = (payload.get("hits") or {}).get("hits") or []
    for item in hits:
        meta = item.get("metadata") or {}
        title = _clean(meta.get("title"))
        if not title:
            continue
        published = _parse_date(meta.get("publication_date"))
        if published and published < cutoff:
            continue
        doi = _clean(meta.get("doi"))
        creators = [
            _clean(c.get("name")) for c in meta.get("creators") or [] if _clean(c.get("name"))
        ]
        links = item.get("links") or {}
        papers.append(
            Paper(
                uid=f"zenodo:{item.get('id') or doi or title[:40]}",
                source="zenodo",
                title=title,
                abstract=_strip_tags(meta.get("description")),
                authors=creators,
                published=published,
                url=_clean(links.get("self_html")) or (f"https://doi.org/{doi}" if doi else ""),
                venue=_clean(meta.get("journal_title")) or _clean(meta.get("resource_type", {}).get("title") if isinstance(meta.get("resource_type"), dict) else "") or "Zenodo",
            )
        )
    return papers


async def search_zenodo(http: HttpClient, query: str, limit: int, cutoff: date) -> list[Paper]:
    data = await http.get_json(
        ZENODO_API, params={"q": query, "size": limit, "sort": "mostrecent"}
    )
    return parse_zenodo(data, cutoff)


def is_ieee(paper: Paper) -> bool:
    """IEEE 出版物：DOI 前缀 10.1109，或 venue / URL 含 IEEE。"""
    url = (paper.url or "").lower()
    venue = (paper.venue or "").lower()
    return "10.1109" in url or "ieee" in venue


async def fetch_all(
    http: HttpClient,
    settings: Settings,
    jobs: list[tuple[str, str]],
    days: int,
) -> tuple[list[Paper], list[str]]:
    """并发抓取多个 (source, query) 任务，单个源失败不影响整体。"""
    cutoff = date.today() - timedelta(days=days)
    limit = settings.per_source_limit
    errors: list[str] = []
    # 限制并发，避免多个大响应同时驻留内存
    semaphore = asyncio.Semaphore(4)

    async def run_one(source: str, query: str) -> list[Paper]:
        async with semaphore:
            try:
                if source == "arxiv":
                    return await search_arxiv(http, query, limit, cutoff)
                if source == "openreview":
                    return await search_openreview(http, query, limit, cutoff)
                if source == "openalex":
                    return await search_openalex(
                        http, query, limit, cutoff, settings.contact_email
                    )
                if source == "crossref":
                    return await search_crossref(
                        http, query, limit, cutoff, settings.contact_email
                    )
                if source == "europepmc":
                    return await search_europepmc(http, query, limit, cutoff)
                if source == "pubmed":
                    return await search_pubmed(
                        http, query, limit, cutoff,
                        settings.ncbi_api_key, settings.contact_email,
                    )
                if source == "semantic_scholar":
                    # 免费额度约 1 rps，任务间串行一点更稳
                    await asyncio.sleep(1.1)
                    return await search_semantic_scholar(
                        http, query, limit, cutoff, settings.semantic_scholar_api_key
                    )
                if source == "hackernews":
                    return await search_hackernews(http, query, limit, cutoff)
                if source == "doaj":
                    return await search_doaj(http, query, limit, cutoff)
                if source == "biorxiv":
                    return await search_biorxiv(http, query, limit, cutoff)
                if source == "hal":
                    return await search_hal(http, query, limit, cutoff)
                if source == "zenodo":
                    return await search_zenodo(http, query, limit, cutoff)
            except Exception as exc:  # 单源故障降级，不拖垮整轮
                errors.append(f"{source}:{query} -> {type(exc).__name__}: {exc}")
            return []

    batches = await asyncio.gather(*(run_one(s, q) for s, q in jobs))
    papers = [paper for batch in batches for paper in batch]
    return papers, errors
