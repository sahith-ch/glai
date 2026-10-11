from roadmap_agent import progress as pg
from roadmap_agent.schemas import Evaluation, Review, Task


def T(**kw):
    base = dict(id="1.1", title="t", description="d", estimated_hours=2, deliverable="x")
    return Task(**{**base, **kw})


# base points by difficulty
assert [pg.max_points(T(difficulty=d)) for d in ("easy", "medium", "hard")] == [10, 20, 40]
# support work earns half
assert pg.max_points(T(difficulty="hard", kind="remedial")) == 20
assert pg.max_points(T(difficulty="medium", topic="Skills check")) == 10
# older plans without a rating: projects are hard, otherwise by hours
assert pg.difficulty(T(topic="Mini project: sales summary", estimated_hours=1)) == "hard"
assert [pg.difficulty(T(estimated_hours=h)) for h in (1.5, 2.5, 3.6)] == ["easy", "medium", "hard"]
# failures cost 25% each, never below half
assert [pg.award(T(difficulty="hard", fail_count=n)) for n in (0, 1, 2, 5)] == [40, 30, 20, 20]


def ev(score):
    return Evaluation(at="x", source="s", passed=True, tests_passed=3, tests_total=3, review=Review(score=score))


# the code review scales 0.8x..1.2x
assert [pg.award(T(difficulty="hard", evaluations=[ev(s)])) for s in (0, 50, 100)] == [32, 40, 48]
# harder always beats easier for the same performance
assert pg.award(T(difficulty="easy", evaluations=[ev(100)])) < pg.award(T(difficulty="medium", evaluations=[ev(0)]))
# earned: 0 until done; awarded value is locked in; older done tasks get it computed
assert pg.earned(T(difficulty="hard")) == 0
assert pg.earned(T(difficulty="hard", status="done", points=33)) == 33
assert pg.earned(T(difficulty="hard", status="done")) == 40
print("ALL OK")
