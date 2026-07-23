from typing import Any


class OpenSearchTraceMapper:

    def map(
        self,
        trace: dict[str, Any],
    ) -> dict[str, Any]:

        return {
            "trace_id": trace["trace_id"],
            "root_span": trace["root_span"],
            "services": trace["services"],
            "span_count": trace["span_count"],
            "duration_ms": trace["duration_ms"],
            "errors": trace["errors"],

            "spans": self.map_spans_for_storage(
                trace.get(
                    "spans",
                    {},
                )
            ),
        }


    def map_spans_for_storage(
        self,
        spans: Any,
    ) -> list[dict[str, Any]]:

        if spans is None:
            return []

        if isinstance(
            spans,
            list,
        ):
            return [
                dict(span)
                for span in spans
                if isinstance(
                    span,
                    dict,
                )
            ]

        if not isinstance(
            spans,
            dict,
        ):
            raise TypeError(
                "Formato de spans inválido para persistência. "
                f"Tipo recebido={type(spans).__name__}."
            )

        mapped_spans: list[
            dict[str, Any]
        ] = []

        for (
            span_id,
            span_payload,
        ) in spans.items():

            if isinstance(
                span_payload,
                dict,
            ):
                mapped_span = dict(
                    span_payload
                )

            else:
                mapped_span = {
                    "value": span_payload,
                }

            mapped_span.setdefault(
                "span_id",
                str(span_id),
            )

            children = mapped_span.get(
                "children"
            )

            if children is None:
                mapped_span[
                    "children"
                ] = []

            elif not isinstance(
                children,
                list,
            ):
                mapped_span[
                    "children"
                ] = [
                    str(children)
                ]

            mapped_spans.append(
                mapped_span
            )

        return mapped_spans