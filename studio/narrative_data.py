"""Evidence data contracts and deterministic chart selection; no orchestration."""

import re
from pydantic import Field
from .models import StrictModel
from .content import numeric_column


class DataRow(StrictModel):
    fact_id: str
    label: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=100)


def choose_visualization(table, relationship):
    numeric = numeric_column(table)
    if not numeric or relationship == "table":
        return "table"
    _, values, unit = numeric
    if relationship == "time":
        # Only recognisable dates can claim a temporal axis.
        if all(
            re.search(
                r"\d{4}|квартал|месяц|январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр|Q[1-4]",
                r[0],
                re.I,
            )
            for r in table.rows
        ):
            return "line"
        return "table"
    if (
        relationship == "share"
        and unit == "%"
        and abs(sum(values) - 100) < 0.01
        and len(values) <= 5
    ):
        return "pie"
    return "bar" if max(map(len, (r[0] for r in table.rows))) > 14 else "column"
