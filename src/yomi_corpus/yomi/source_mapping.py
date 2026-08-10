from __future__ import annotations

from dataclasses import dataclass


ASCII_SPACE = " "
NBSP = "\u00a0"


class SourceSurfaceMappingError(ValueError):
    pass


@dataclass(frozen=True)
class SourceTextMapping:
    source_text: str
    analysis_text: str

    def __post_init__(self) -> None:
        if len(self.source_text) != len(self.analysis_text):
            raise SourceSurfaceMappingError(
                "analysis normalization changed text length: "
                f"source={len(self.source_text)}, analysis={len(self.analysis_text)}"
            )
        for offset, (source_char, analysis_char) in enumerate(
            zip(self.source_text, self.analysis_text, strict=True)
        ):
            if source_char == analysis_char:
                continue
            if source_char == ASCII_SPACE and analysis_char == NBSP:
                continue
            raise SourceSurfaceMappingError(
                "analysis normalization changed a non-space source character "
                f"at offset {offset}: source={source_char!r}, analysis={analysis_char!r}"
            )

    def restore_partition(
        self,
        surfaces: list[str],
        *,
        stage: str,
    ) -> list[str]:
        if any(not surface for surface in surfaces):
            index = next(index for index, surface in enumerate(surfaces) if not surface)
            raise SourceSurfaceMappingError(
                f"{stage} returned an empty surface at token {index}"
            )
        reconstructed = "".join(surfaces)
        if reconstructed != self.analysis_text:
            offset = first_difference_offset(reconstructed, self.analysis_text)
            raise SourceSurfaceMappingError(
                f"{stage} surfaces do not reproduce analysis input at offset {offset}: "
                f"got={context_at(reconstructed, offset)!r}, "
                f"expected={context_at(self.analysis_text, offset)!r}"
            )
        restored: list[str] = []
        cursor = 0
        for surface in surfaces:
            end = cursor + len(surface)
            restored.append(self.source_text[cursor:end])
            cursor = end
        if "".join(restored) != self.source_text:
            raise SourceSurfaceMappingError(
                f"{stage} restored surfaces do not reproduce source text"
            )
        return restored


def first_difference_offset(left: str, right: str) -> int:
    shared = min(len(left), len(right))
    for offset in range(shared):
        if left[offset] != right[offset]:
            return offset
    return shared


def context_at(text: str, offset: int, *, radius: int = 20) -> str:
    return text[max(0, offset - radius) : offset + radius]
