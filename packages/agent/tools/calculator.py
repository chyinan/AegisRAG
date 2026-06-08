from __future__ import annotations

import ast
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.agent.dto import ToolDefinition, ToolRateLimit
from packages.common.context import AuthenticatedRequestContext

CALCULATOR_PERMISSION = "agent:tool:calculator"
CALCULATOR_INVALID_EXPRESSION = "CALCULATOR_INVALID_EXPRESSION"
CALCULATOR_UNSUPPORTED_EXPRESSION = "CALCULATOR_UNSUPPORTED_EXPRESSION"
CALCULATOR_DIVISION_BY_ZERO = "CALCULATOR_DIVISION_BY_ZERO"
CALCULATOR_COMPLEXITY_LIMIT_EXCEEDED = "CALCULATOR_COMPLEXITY_LIMIT_EXCEEDED"
CALCULATOR_RESULT_OUT_OF_RANGE = "CALCULATOR_RESULT_OUT_OF_RANGE"

_MAX_EXPRESSION_LENGTH = 256
_MAX_AST_NODES = 64
_MAX_AST_DEPTH = 16
_MAX_NUMBER_DIGITS = 18
_MAX_EXPONENT_ABS = 20
_MAX_RESULT_ABS = Decimal("1000000000000")
_DECIMAL_PRECISION = 28


class CalculatorInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expression: str = Field(min_length=1, max_length=_MAX_EXPRESSION_LENGTH)

    @field_validator("expression")
    @classmethod
    def _expression_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("expression must not be blank")
        return normalized


class CalculatorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["success", "error"]
    result: str | None = None
    result_type: Literal["integer", "decimal"] | None = None
    operation_summary: str = ""
    error_code: str | None = None
    message: str | None = None


def build_calculator_tool(
    *,
    timeout_seconds: float,
    rate_limit: ToolRateLimit,
) -> ToolDefinition:
    async def handler(
        payload: CalculatorInput,
        context: AuthenticatedRequestContext,
    ) -> CalculatorOutput:
        _ = context
        try:
            result = _evaluate_expression(payload.expression)
        except CalculatorEvaluationError as exc:
            return CalculatorOutput(
                status="error",
                result=None,
                result_type=None,
                operation_summary="arithmetic_expression_rejected",
                error_code=exc.code,
                message=exc.safe_message,
            )

        result_text, result_type = _format_result(result)
        return CalculatorOutput(
            status="success",
            result=result_text,
            result_type=result_type,
            operation_summary="arithmetic_expression_evaluated",
        )

    return ToolDefinition(
        name="calculator",
        description="Evaluate bounded deterministic arithmetic expressions.",
        input_schema=CalculatorInput,
        output_schema=CalculatorOutput,
        permission=CALCULATOR_PERMISSION,
        timeout_seconds=timeout_seconds,
        rate_limit=rate_limit,
        handler=handler,
    )


class CalculatorEvaluationError(ValueError):
    def __init__(self, *, code: str, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


def _evaluate_expression(expression: str) -> Decimal:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalculatorEvaluationError(
            code=CALCULATOR_INVALID_EXPRESSION,
            safe_message="invalid_arithmetic_expression",
        ) from exc

    _validate_complexity(tree)
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        try:
            result = _evaluate_node(tree.body, expression)
        except DivisionByZero as exc:
            raise CalculatorEvaluationError(
                code=CALCULATOR_DIVISION_BY_ZERO,
                safe_message="division_by_zero",
            ) from exc
        except InvalidOperation as exc:
            raise CalculatorEvaluationError(
                code=CALCULATOR_UNSUPPORTED_EXPRESSION,
                safe_message="unsupported_arithmetic_expression",
            ) from exc

    if not result.is_finite() or abs(result) > _MAX_RESULT_ABS:
        raise CalculatorEvaluationError(
            code=CALCULATOR_RESULT_OUT_OF_RANGE,
            safe_message="calculator_result_out_of_range",
        )
    return result


def _validate_complexity(tree: ast.AST) -> None:
    node_count = 0
    for node in ast.walk(tree):
        node_count += 1
        if node_count > _MAX_AST_NODES:
            raise CalculatorEvaluationError(
                code=CALCULATOR_COMPLEXITY_LIMIT_EXCEEDED,
                safe_message="calculator_complexity_limit_exceeded",
            )
        if not isinstance(
            node,
            ast.Expression
            | ast.BinOp
            | ast.UnaryOp
            | ast.Constant
            | ast.Add
            | ast.Sub
            | ast.Mult
            | ast.Div
            | ast.FloorDiv
            | ast.Mod
            | ast.Pow
            | ast.UAdd
            | ast.USub
            | ast.Load,
        ):
            raise CalculatorEvaluationError(
                code=CALCULATOR_UNSUPPORTED_EXPRESSION,
                safe_message="unsupported_arithmetic_expression",
            )
    if _depth(tree) > _MAX_AST_DEPTH:
        raise CalculatorEvaluationError(
            code=CALCULATOR_COMPLEXITY_LIMIT_EXCEEDED,
            safe_message="calculator_complexity_limit_exceeded",
        )


def _depth(node: ast.AST) -> int:
    children = list(ast.iter_child_nodes(node))
    if not children:
        return 1
    return 1 + max(_depth(child) for child in children)


def _evaluate_node(node: ast.AST, expression: str) -> Decimal:
    if isinstance(node, ast.Constant):
        return _constant_value(node, expression)
    if isinstance(node, ast.UnaryOp):
        operand = _evaluate_node(node.operand, expression)
        if isinstance(node.op, ast.UAdd):
            return operand
        if isinstance(node.op, ast.USub):
            return -operand
        raise _unsupported()
    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left, expression)
        right = _evaluate_node(node.right, expression)
        return _apply_binary_operator(node.op, left, right)
    raise _unsupported()


def _constant_value(node: ast.Constant, expression: str) -> Decimal:
    value = node.value
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _unsupported()
    text = ast.get_source_segment(expression, node) or str(value)
    if "_" in text:
        raise _unsupported()
    digits = text.replace("-", "").replace("+", "").replace(".", "")
    if "e" in text.lower() or len(digits) > _MAX_NUMBER_DIGITS:
        raise CalculatorEvaluationError(
            code=CALCULATOR_COMPLEXITY_LIMIT_EXCEEDED,
            safe_message="calculator_complexity_limit_exceeded",
        )
    return Decimal(text)


def _apply_binary_operator(operator: ast.operator, left: Decimal, right: Decimal) -> Decimal:
    if isinstance(operator, ast.Add):
        return left + right
    if isinstance(operator, ast.Sub):
        return left - right
    if isinstance(operator, ast.Mult):
        return left * right
    if isinstance(operator, ast.Div):
        if right == 0:
            raise CalculatorEvaluationError(
                code=CALCULATOR_DIVISION_BY_ZERO,
                safe_message="division_by_zero",
            )
        return left / right
    if isinstance(operator, ast.FloorDiv):
        if right == 0:
            raise CalculatorEvaluationError(
                code=CALCULATOR_DIVISION_BY_ZERO,
                safe_message="division_by_zero",
            )
        return left // right
    if isinstance(operator, ast.Mod):
        if right == 0:
            raise CalculatorEvaluationError(
                code=CALCULATOR_DIVISION_BY_ZERO,
                safe_message="division_by_zero",
            )
        return left % right
    if isinstance(operator, ast.Pow):
        return _power(left, right)
    raise _unsupported()


def _power(left: Decimal, right: Decimal) -> Decimal:
    if right != right.to_integral_value():
        raise _unsupported()
    exponent = int(right)
    if abs(exponent) > _MAX_EXPONENT_ABS:
        raise CalculatorEvaluationError(
            code=CALCULATOR_COMPLEXITY_LIMIT_EXCEEDED,
            safe_message="calculator_complexity_limit_exceeded",
        )
    return left**exponent


def _format_result(result: Decimal) -> tuple[str, Literal["integer", "decimal"]]:
    if result == result.to_integral_value():
        return str(int(result)), "integer"
    return format(result.normalize(), "f"), "decimal"


def _unsupported() -> CalculatorEvaluationError:
    return CalculatorEvaluationError(
        code=CALCULATOR_UNSUPPORTED_EXPRESSION,
        safe_message="unsupported_arithmetic_expression",
    )
