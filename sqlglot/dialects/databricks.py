from __future__ import annotations

from copy import deepcopy
from collections import defaultdict

from sqlglot import exp, transforms, jsonpath
from sqlglot.dialects.dialect import (
    date_delta_sql,
    build_date_delta,
    timestamptrunc_sql,
    build_formatted_time,
    groupconcat_sql,
)
from sqlglot.dialects.spark import Spark
from sqlglot.tokens import TokenType
from sqlglot.optimizer.annotate_types import TypeAnnotator


def _jsonextract_sql(
    self: Databricks.Generator, expression: exp.JSONExtract | exp.JSONExtractScalar
) -> str:
    this = self.sql(expression, "this")
    expr = self.sql(expression, "expression")
    return f"{this}:{expr}"


class Databricks(Spark):
    SAFE_DIVISION = False
    COPY_PARAMS_ARE_CSV = False

    COERCES_TO = defaultdict(set, deepcopy(TypeAnnotator.COERCES_TO))
    for text_type in exp.DataType.TEXT_TYPES:
        COERCES_TO[text_type] |= {
            *exp.DataType.NUMERIC_TYPES,
            *exp.DataType.TEMPORAL_TYPES,
            exp.DataType.Type.BINARY,
            exp.DataType.Type.BOOLEAN,
            exp.DataType.Type.INTERVAL,
        }

    class JSONPathTokenizer(jsonpath.JSONPathTokenizer):
        IDENTIFIERS = ["`", '"']

    class Tokenizer(Spark.Tokenizer):
        KEYWORDS = {
            **Spark.Tokenizer.KEYWORDS,
            "VOID": TokenType.VOID,
        }

    class Parser(Spark.Parser):
        LOG_DEFAULTS_TO_LN = True
        STRICT_CAST = True
        COLON_IS_VARIANT_EXTRACT = True

        FUNCTIONS = {
            **Spark.Parser.FUNCTIONS,
            "DATEADD": build_date_delta(exp.DateAdd),
            "DATE_ADD": build_date_delta(exp.DateAdd),
            "DATEDIFF": build_date_delta(exp.DateDiff),
            "DATE_DIFF": build_date_delta(exp.DateDiff),
            "TO_DATE": build_formatted_time(exp.TsOrDsToDate, "databricks"),
        }

        FACTOR = {
            **Spark.Parser.FACTOR,
            TokenType.COLON: exp.JSONExtract,
        }

    class Generator(Spark.Generator):
        TABLESAMPLE_SEED_KEYWORD = "REPEATABLE"
        COPY_PARAMS_ARE_WRAPPED = False
        COPY_PARAMS_EQ_REQUIRED = True
        JSON_PATH_SINGLE_QUOTE_ESCAPE = False
        QUOTE_JSON_PATH = False
        PARSE_JSON_NAME = "PARSE_JSON"

        TRANSFORMS = {
            **Spark.Generator.TRANSFORMS,
            exp.DateAdd: date_delta_sql("DATEADD"),
            exp.DateDiff: date_delta_sql("DATEDIFF"),
            exp.DatetimeAdd: lambda self, e: self.func(
                "TIMESTAMPADD", e.unit, e.expression, e.this
            ),
            exp.DatetimeSub: lambda self, e: self.func(
                "TIMESTAMPADD",
                e.unit,
                exp.Mul(this=e.expression, expression=exp.Literal.number(-1)),
                e.this,
            ),
            exp.DatetimeTrunc: timestamptrunc_sql(),
            exp.GroupConcat: groupconcat_sql,
            exp.Select: transforms.preprocess(
                [
                    transforms.eliminate_distinct_on,
                    transforms.unnest_to_explode,
                    transforms.any_to_exists,
                ]
            ),
            exp.JSONExtract: _jsonextract_sql,
            exp.JSONExtractScalar: _jsonextract_sql,
            exp.JSONPathRoot: lambda *_: "",
            exp.ToChar: lambda self, e: self.function_fallback_sql(e),
        }

        TRANSFORMS.pop(exp.TryCast)

        TYPE_MAPPING = {
            **Spark.Generator.TYPE_MAPPING,
            exp.DataType.Type.NULL: "VOID",
        }

        def columndef_sql(self, expression: exp.ColumnDef, sep: str = " ") -> str:
            constraint = expression.find(exp.GeneratedAsIdentityColumnConstraint)
            kind = expression.kind
            if (
                constraint
                and isinstance(kind, exp.DataType)
                and kind.this in exp.DataType.INTEGER_TYPES
            ):
                # only BIGINT generated identity constraints are supported
                expression.set("kind", exp.DataType.build("bigint"))

            return super().columndef_sql(expression, sep)

        def generatedasidentitycolumnconstraint_sql(
            self, expression: exp.GeneratedAsIdentityColumnConstraint
        ) -> str:
            expression.set("this", True)  # trigger ALWAYS in super class
            return super().generatedasidentitycolumnconstraint_sql(expression)

        def jsonpath_sql(self, expression: exp.JSONPath) -> str:
            expression.set("escape", None)
            return super().jsonpath_sql(expression)
