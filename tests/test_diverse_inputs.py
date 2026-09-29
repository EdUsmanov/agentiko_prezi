"""Deterministic generated templates exercise geometry and content invariants."""

import shutil

import pytest

from studio.checks.audit import audit_scenes
from studio.composition.composer import compose_variant
from studio.pipeline import load_package, prepare
from studio.contents.planner import assign_compositions, extractive_plans, validate_plans
from studio.security import InputRejected
from studio.jobs.store import Store
from studio.templates.parsing import analyze_template
from test_support.inputs import make_template

CASES = [
    (
        7,
        "wide",
        1,
        False,
        "pptx",
        "# Проект\n## Контекст\nКоманда обрабатывает 40 заявок.\n## Срок\nПилот длится 12 недель.\n## Итог\nРезультат проверяют вручную.",
    ),
    (
        37,
        "standard",
        2,
        False,
        "potx",
        "# Delivery\n## Context\nThe team reviews 40 requests.\n## Timing\nThe pilot runs for 12 weeks.\n## Outcome\nReviewers verify the result.",
    ),
    (
        91,
        "wide",
        3,
        True,
        "pptx",
        "# Mixed project\n## Scope\nКоманда reviews 40 заявок.\n## Timing\nPilot длится 12 weeks.\n## Проверка\nReviewers проверяют результат.",
    ),
    (
        123,
        "standard",
        1,
        True,
        "potx",
        "# Данные\n## Контекст\nКоманда оценивает пилот.\n## Сравнение\n| Канал | Заявки |\n|---|---|\n| Север | 20 |\n| Юг | 30 |\n## Решение\nПроверка займёт 12 недель.",
    ),
]


@pytest.mark.parametrize(
    "seed,aspect,columns,dark,extension,text",
    CASES,
    ids=["wide-ru", "standard-en", "dark-mixed-columns", "standard-dark-table"],
)
def test_generated_inputs_preserve_sources_and_fit_canvas(
    tmp_path, seed, aspect, columns, dark, extension, text
):
    template = make_template(
        tmp_path / (f"random-{seed}." + extension),
        seed=seed,
        aspect=aspect,
        columns=columns,
        dark=dark,
    )
    store = Store(tmp_path / "data")
    job = store.create("preparation", {"template_name": template.name})
    shutil.copyfile(template, store.directory(job["id"]) / "input.pptx")
    prepare(store, job["id"], text, "Reviewers", "", 3)
    assert store.get(job["id"])["state"] == "ready", store.get(job["id"])
    package = load_package(store, job["id"])
    assert package.template.width / package.template.height == pytest.approx(
        13.333 / 7.5 if aspect == "wide" else 4 / 3, abs=0.001
    )
    plans = validate_plans(assign_compositions(extractive_plans(package), package), package)
    expected = {fact.id for fact in package.content.facts}
    for variant in plans.variants:
        assert {fact for slide in variant.slides for fact in slide.fact_ids} == expected
        scenes = compose_variant(variant, package)
        findings = audit_scenes(scenes, package)
        assert not [finding for finding in findings if finding.severity == "error"], findings
        assert "SYNTHETIC OLD CONTENT" not in str([scene.model_dump() for scene in scenes])


def test_identical_bytes_under_unfamiliar_filename_keep_same_layout_contract(tmp_path):
    source = make_template(tmp_path / "known.pptx", seed=444, columns=2)
    renamed = tmp_path / "организатор совершенно другой шаблон.pptx"
    renamed.write_bytes(source.read_bytes())
    profiles = [
        analyze_template(path, tmp_path / str(index))
        for index, path in enumerate((source, renamed))
    ]
    left, right = profiles
    assert left.sha256 == right.sha256
    assert [p.model_dump() for p in left.patterns] == [p.model_dump() for p in right.patterns]
    assert left.color_roles == right.color_roles
    assert (left.width, left.height, left.font, left.margin) == (
        right.width,
        right.height,
        right.font,
        right.margin,
    )


def test_generator_reproduces_bytes_from_seed(tmp_path):
    first = make_template(tmp_path / "a.pptx", seed=909, columns=3, dark=True)
    second = make_template(tmp_path / "b.pptx", seed=909, columns=3, dark=True)
    assert first.read_bytes() == second.read_bytes()


def test_invalid_pptx_is_rejected_before_layout_analysis(tmp_path):
    broken = tmp_path / "broken.pptx"
    broken.write_bytes(b"This is not an Office archive")
    with pytest.raises(InputRejected):
        analyze_template(broken, tmp_path / "artifacts")
