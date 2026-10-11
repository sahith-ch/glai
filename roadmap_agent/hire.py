"""Hiring search: rank learners by the skills they have acquired (served by server.py).

    GET /api/search?q=pandas,sql   candidates ranked by acquired skills matching the terms
    GET /api/candidates/<id>       one profile: stats, skill graph, and the tasks behind every skill

Candidates are profile JSON files: every *.json in candidates/ plus the local user_profile.json.
Only *acquired* skills count - earned by finished, tested work (or verified by the skills check).
"""

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import user, vector
from .schemas import SkillGraph, SkillNode, TaskEvidence, UserProfile

CANDIDATES_DIR = "candidates"


@dataclass
class Candidate:
    id: str
    profile: UserProfile
    # Earned skills plus the ones in progress, with the finished tasks behind each (user.learning) - the same
    # graph the learner sees. Search and ranking still use profile.skill_graph: earned skills only.
    learning: SkillGraph | None = None


@dataclass
class Evidence:
    node: SkillNode
    task: TaskEvidence | None  # the solved task that matched, for semantic hits on task text
    score: float
    how: str  # "keyword" | "semantic"


KEYWORD_SCORE = 10.0  # an exact keyword match outranks any semantic score


# ---------------------------------------------------------------- data


def load_candidates(root: Path, today: date) -> list[Candidate]:
    """The learner ("me") and everyone to rank against. A folder: me + candidates/. Deployed, the storage
    (supa.Folder / supa.Public) knows its population: every public profile."""
    if hasattr(root, "candidates"):
        return root.candidates(today)
    out = []
    if user.profile_path(root).exists():  # the local learner, kept up to date from their roadmaps
        me = user.rebuild(root, today)
        out.append(Candidate("me", me, user.learning_graph(root, me)))
    from . import cloud

    others = cloud.public_others(root)  # signed in to GLAI Hire: rank against everyone there
    if others is not None:
        return out + others
    for p in sorted((root / CANDIDATES_DIR).glob("*.json")):
        try:
            out.append(Candidate(p.stem, UserProfile.model_validate_json(p.read_text(encoding="utf-8"))))
        except Exception:  # one broken file shouldn't take the site down
            continue
    return out


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#]+", text.lower())


def _matches(term: str, node: SkillNode) -> bool:
    """Every word of the term starts a word of the skill (so 'visual' finds 'visualisation', 'sql' finds 'SQL joins')."""
    skill_words = _words(node.name)
    return all(any(sw.startswith(tw[:6]) for sw in skill_words) for tw in _words(term))


# ---------------------------------------------------------------- understanding the query

# A role is the skills it takes. Recruiters search for "frontend developer"; the evidence is per skill, so a
# role is expanded into the skills that make it up and a candidate is matched on how many they have EARNED.
# (Matching the role against a self-written headline would match a claim, not proof.)
ROLES: dict[str, list[str]] = {
    "frontend developer": ["html", "css", "javascript", "react", "responsive layouts", "dom manipulation"],
    "backend developer": ["python", "sql joins", "database schema design", "rest apis", "error handling"],
    "full stack developer": ["html", "css", "javascript", "react", "rest apis", "sql", "database schema design"],
    "web developer": ["html", "css", "javascript", "responsive layouts", "rest apis"],
    "data analyst": ["sql", "excel", "pandas", "data visualisation", "statistics", "cleaning data"],
    "data scientist": ["python", "pandas", "statistics", "machine learning", "data visualisation", "numpy"],
    "machine learning engineer": ["python", "numpy", "machine learning", "model evaluation", "deep learning", "pytorch"],
    "data engineer": ["sql", "python", "etl pipelines", "database schema design", "spark", "data warehousing"],
    "devops engineer": ["linux", "docker", "ci/cd", "git", "cloud", "bash scripting"],
    "software engineer": ["data structures", "algorithms", "python", "git", "testing", "object oriented programming"],
}
ROLE_ALIASES = {
    "frontend": "frontend developer", "front end": "frontend developer", "front-end": "frontend developer",
    "ui developer": "frontend developer", "frontend engineer": "frontend developer", "react developer": "frontend developer",
    "front end developer": "frontend developer", "front-end developer": "frontend developer",
    "backend": "backend developer", "back end": "backend developer", "back-end": "backend developer",
    "backend engineer": "backend developer", "back end developer": "backend developer", "back-end developer": "backend developer",
    "full stack": "full stack developer", "fullstack": "full stack developer", "full-stack": "full stack developer",
    "fullstack developer": "full stack developer", "full-stack developer": "full stack developer",
    "analyst": "data analyst", "business analyst": "data analyst", "ml engineer": "machine learning engineer",
    "ai engineer": "machine learning engineer", "devops": "devops engineer",
    "sde": "software engineer", "software developer": "software engineer", "programmer": "software engineer",
}
ROLE_COVERAGE = 0.5  # a candidate fits a role when they have earned at least half of its skills
_ROLE_NOUN = re.compile(r"\b(developer|engineer|analyst|scientist|designer|architect|administrator|programmer|dev)s?\b", re.I)
_LEVEL = re.compile(r"\b(junior|senior|mid[- ]?level|entry[- ]?level|intern|experienced|lead|principal|fresher)\b", re.I)
_FILLER = re.compile(r"\b(someone|who|knows?|good|strong|skilled|experience|skills?|in|at|of|the|a|an|for|looking|need|needs)\b", re.I)


@dataclass
class Term:
    text: str  # as the recruiter wrote it
    kind: str  # "skill" | "role"
    skills: list[str]  # what is matched: the term itself, or the role's skills


def _clean(part: str) -> str:
    return re.sub(r"\s+", " ", _FILLER.sub(" ", _LEVEL.sub(" ", part))).strip(" .-")


def _term(part: str) -> Term | None:
    text = _clean(part)
    if not _words(text):
        return None
    key = text.lower()
    role = ROLE_ALIASES.get(key, key if key in ROLES else None)
    if role:
        return Term(part.strip(), "role", ROLES[role])
    if _ROLE_NOUN.search(text):  # "rust developer": the role is its skill
        skill = _ROLE_NOUN.sub(" ", text).strip()
        return Term(part.strip(), "skill", [skill]) if _words(skill) else None
    return Term(part.strip(), "skill", [text])


def parse_query(query: str, known: list[str] | None = None) -> list[Term]:
    """Split a recruiter's query into skills and roles. Commas always split; "and", "with", "&", "or" and " + "
    split too, unless the whole phrase is a skill someone has (so "data structures and algorithms" stays one)."""
    known_names = [k.lower() for k in known or []]
    terms: list[Term] = []
    for part in re.split(r"[,;\n]", query):
        if not _words(part):
            continue
        whole = _clean(part).lower()
        if any(whole and whole in name for name in known_names) or whole in ROLES or whole in ROLE_ALIASES:
            pieces = [part]
        else:
            pieces = re.split(r"\band\b|\bwith\b|&|\s\+\s|\bplus\b|\bor\b", part, flags=re.I)
        for piece in (seg for p in pieces for seg in _split_spaces(p, known_names)):
            t = _term(piece)
            if t and t.text.lower() not in {x.text.lower() for x in terms}:
                terms.append(t)
    return terms


def _known(phrase: str, known_names: list[str]) -> bool:
    """A phrase names a skill someone has (every word starts a word of it) or a role."""
    key = _clean(phrase).lower()
    if key in ROLES or key in ROLE_ALIASES:
        return True
    words = _words(key)
    return bool(words) and any(all(any(nw.startswith(w[:6]) for nw in _words(name)) for w in words)
                               for name in known_names)


def _split_spaces(piece: str, known_names: list[str]) -> list[str]:
    """"sql pandas" is two skills, "machine learning" one: when the whole piece names nothing, split it into the
    longest runs of words that do. Kept whole unless every run is known (so unknown phrases still match as typed)."""
    words = _clean(piece).split()
    if len(words) < 2 or not known_names or _known(piece, known_names) or _ROLE_NOUN.search(piece):
        return [piece]
    runs, i = [], 0
    while i < len(words):
        j = next((j for j in range(len(words), i, -1) if _known(" ".join(words[i:j]), known_names)), None)
        if j is None:
            return [piece]
        runs.append(" ".join(words[i:j]))
        i = j
    return runs


def terms_of(query: str) -> list[str]:
    return [t.text for t in parse_query(query)]


def _merge(found: list[Evidence]) -> list[Evidence]:
    best: dict[tuple, Evidence] = {}
    for e in found:
        key = (e.node.id, e.task.id if e.task else None)
        if key not in best or e.score > best[key].score:
            best[key] = e
    return sorted(best.values(), key=lambda e: -e.score)


# ---------------------------------------------------------------- matching and ranking


@dataclass
class TermMatch:
    term: Term
    evidence: list[Evidence]  # best first
    covered: list[str] = field(default_factory=list)  # for a role: its skills this candidate has earned
    missing: list[str] = field(default_factory=list)

    @property
    def fit(self) -> float:
        """1 for a matched skill; for a role, the share of its skills covered."""
        if self.term.kind == "skill":
            return 1.0 if self.evidence else 0.0
        return len(self.covered) / len(self.term.skills)


def _skill_evidence(candidates: list[Candidate], skills: list[str], engine) -> dict[str, dict[str, list[Evidence]]]:
    """Per candidate, per skill phrase: the earned skills (and solved tasks) that show it, best first."""
    acquired = {c.id: [n for n in c.profile.skill_graph.nodes if n.status in ("completed", "verified")] for c in candidates}
    found: dict[str, dict[str, list[Evidence]]] = {c.id: {} for c in candidates}
    for s in skills:  # keyword matches always count
        for c in candidates:
            found[c.id][s] = [Evidence(n, None, KEYWORD_SCORE, "keyword") for n in acquired[c.id] if _matches(s, n)]
    if engine is not None:  # semantic: score everyone first, then accept relative to the best match per phrase
        indexed = {}
        for c in candidates:
            docs = vector.docs_of(c.profile)
            indexed[c.id] = (docs, engine.embed_docs([d.text for d in docs]))
        for s in skills:
            qvec = engine.embed_query(s)
            scored = {cid: engine.candidates(s, qvec, docs, vecs) for cid, (docs, vecs) in indexed.items()}
            for cid, hits in vector.accept(scored).items():
                found[cid][s] += [Evidence(h.doc.node, h.doc.task, h.score, "semantic") for h in hits]
    return {cid: {s: _merge(es) for s, es in per.items()} for cid, per in found.items()}


@dataclass
class Ranked:
    candidate: Candidate
    terms: list[TermMatch]  # the terms this candidate matches

    @property
    def fit(self) -> float:
        return sum(t.fit for t in self.terms)

    @property
    def depth(self) -> int:
        """Points earned in the skills that matched (a skill counted once, a whole skill not on top of its parts)."""
        nodes = {e.node.id: e.node for t in self.terms for e in t.evidence}
        parents = {n.parent for n in nodes.values() if n.kind == "subskill"}
        return sum(n.points for n in nodes.values() if not (n.kind == "skill" and n.name in parents))

    @property
    def strength(self) -> float:
        best = [t.evidence[0].score for t in self.terms if t.evidence]
        return sum(best) / len(best) if best else 0.0


def search(candidates: list[Candidate], query: str) -> tuple[list[Ranked], str, list[Term]]:
    """Candidates ranked by: how much of the query they cover (roles by the share of their skills), then depth
    (points earned in the matching skills), then match strength. Returns (ranked, mode, parsed terms)."""
    known = [n.name for c in candidates for n in c.profile.skill_graph.nodes]
    terms = parse_query(query, known)
    phrases = list(dict.fromkeys(s for t in terms for s in t.skills))
    engine = vector.get() if phrases else None
    ev = _skill_evidence(candidates, phrases, engine)
    results = []
    for c in candidates:
        matched = []
        for t in terms:
            if t.kind == "skill":
                es = ev[c.id].get(t.skills[0], [])
                if es:
                    matched.append(TermMatch(t, es))
            else:
                covered = [s for s in t.skills if ev[c.id].get(s)]
                if len(covered) >= max(2, ROLE_COVERAGE * len(t.skills)):
                    es = _merge([e for s in covered for e in ev[c.id][s]])
                    matched.append(TermMatch(t, es, covered, [s for s in t.skills if s not in covered]))
        if matched or not terms:
            results.append(Ranked(c, matched))
    ranked = sorted(results, key=lambda r: (-r.fit, -r.depth, -r.strength, -r.candidate.profile.stats.points))
    return ranked, "semantic" if engine else "keyword", terms


# ---------------------------------------------------------------- API payloads


def _task_view(t: TaskEvidence | None) -> dict | None:
    if not t:
        return None
    return {"title": t.title, "tests": t.tests, "review_score": t.review_score, "difficulty": t.difficulty,
            "completed_on": t.completed_on}


def search_payload(candidates: list[Candidate], query: str) -> dict:
    ranked, mode, terms = search(candidates, query)
    out = []
    for r in ranked:
        p = r.candidate.profile
        earned = [n for n in p.skill_graph.nodes if n.status in ("completed", "verified")]
        if terms:
            shown = [e.node.name for t in r.terms for e in t.evidence[:3]]
        else:
            shown = [n.name for n in sorted(earned, key=lambda n: -n.points)[:5]]
        out.append({
            "id": r.candidate.id,
            "name": p.name or r.candidate.id,
            "headline": p.headline,
            "location": p.location,
            "demo": p.demo,
            "points": p.stats.points,
            "tasks_done": p.stats.tasks_done,
            "first_try_rate": p.stats.first_try_rate,
            "avg_code_score": p.stats.avg_code_score,
            "skills_acquired": len(earned),
            "matched_terms": [t.term.text for t in r.terms],
            "missing_terms": [t.text for t in terms if t.text not in {m.term.text for m in r.terms}],
            "skills": list(dict.fromkeys(shown)),
            # why each term matched: the skills, and the solved task behind the best one
            "evidence": [
                {"term": t.term.text, "kind": t.term.kind, "skill": t.evidence[0].node.name,
                 "skill_id": t.evidence[0].node.id, "how": t.evidence[0].how,
                 "task": t.evidence[0].task.title if t.evidence[0].task else None,
                 "proof": _task_view(t.evidence[0].task or (t.evidence[0].node.tasks[-1] if t.evidence[0].node.tasks else None)),
                 "skills": list(dict.fromkeys(e.node.name for e in t.evidence))[:6],
                 "covered": t.covered, "missing": t.missing}
                for t in r.terms
            ],
            "fit": round(r.fit, 2),
            "relevance": r.depth,
        })
    return {"query": query, "mode": mode, "results": out,
            "terms": [t.text for t in terms],
            "parsed": [{"text": t.text, "kind": t.kind, "skills": t.skills} for t in terms]}


def candidate_payload(c: Candidate) -> dict:
    p = c.profile
    return {"id": c.id, **p.model_dump(include={"name", "headline", "location", "contact", "interests", "demo",
                                                 "skill_graph", "stats"}),
            "learning_graph": (c.learning or p.skill_graph).model_dump()}
