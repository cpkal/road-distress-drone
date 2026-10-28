from dataclasses import dataclass
from enum import StrEnum


class UnknownDistressClassError(ValueError):
    """Raised when a model outputs a class name not registered in DISTRESS_CLASSES."""


class DistressClass(StrEnum):
    TRANSVERSE_CRACKING = "transverse_cracking"
    LONGITUDINAL_CRACKING = "longitudinal_cracking"
    ALLIGATOR_CRACKING = "alligator_cracking"
    PATCH = "patch"
    POTHOLE = "pothole"


@dataclass(frozen=True, slots=True)
class DistressClassInfo:
    key: DistressClass
    label: str


DISTRESS_CLASSES: dict[DistressClass, DistressClassInfo] = {
    DistressClass.TRANSVERSE_CRACKING: DistressClassInfo(
        key=DistressClass.TRANSVERSE_CRACKING,
        label="Transverse cracking",
    ),
    DistressClass.LONGITUDINAL_CRACKING: DistressClassInfo(
        key=DistressClass.LONGITUDINAL_CRACKING,
        label="Longitudinal cracking",
    ),
    DistressClass.ALLIGATOR_CRACKING: DistressClassInfo(
        key=DistressClass.ALLIGATOR_CRACKING,
        label="Alligator cracking",
    ),
    DistressClass.PATCH: DistressClassInfo(
        key=DistressClass.PATCH,
        label="Patch",
    ),
    DistressClass.POTHOLE: DistressClassInfo(
        key=DistressClass.POTHOLE,
        label="Pothole",
    ),
}

CRACK_DISTRESS_CLASSES: frozenset[DistressClass] = frozenset(
    {
        DistressClass.TRANSVERSE_CRACKING,
        DistressClass.LONGITUDINAL_CRACKING,
        DistressClass.ALLIGATOR_CRACKING,
    }
)

_MODEL_NAME_LOOKUP: dict[str, DistressClass] = {
    "alligator crack": DistressClass.ALLIGATOR_CRACKING,
    "alligator_crack": DistressClass.ALLIGATOR_CRACKING,
    "block crack": DistressClass.ALLIGATOR_CRACKING,
    "block_crack": DistressClass.ALLIGATOR_CRACKING,
    "block cracking": DistressClass.ALLIGATOR_CRACKING,
    "block_cracking": DistressClass.ALLIGATOR_CRACKING,
    "longitudinal crack": DistressClass.LONGITUDINAL_CRACKING,
    "longitudinal_crack": DistressClass.LONGITUDINAL_CRACKING,
    "transverse crack": DistressClass.TRANSVERSE_CRACKING,
    "transverse_crack": DistressClass.TRANSVERSE_CRACKING,
    "oblique crack": DistressClass.TRANSVERSE_CRACKING,
    "oblique_crack": DistressClass.TRANSVERSE_CRACKING,
    "oblique cracking": DistressClass.TRANSVERSE_CRACKING,
    "oblique_cracking": DistressClass.TRANSVERSE_CRACKING,
    "repair": DistressClass.PATCH,
}
for _cls, _info in DISTRESS_CLASSES.items():
    _MODEL_NAME_LOOKUP[_cls.value] = _cls
    _MODEL_NAME_LOOKUP[_info.label.lower()] = _cls


def normalize_distress_class(raw_name: str) -> DistressClass:
    """Map a model class name to a canonical DistressClass, or raise UnknownDistressClassError."""
    cleaned = " ".join(
        raw_name.strip().lower().replace("-", " ").replace("_", " ").split()
    )
    underscored = cleaned.replace(" ", "_")

    if underscored in _MODEL_NAME_LOOKUP:
        return _MODEL_NAME_LOOKUP[underscored]
    if cleaned in _MODEL_NAME_LOOKUP:
        return _MODEL_NAME_LOOKUP[cleaned]

    valid = ", ".join(c.value for c in DistressClass)
    raise UnknownDistressClassError(
        f"Unknown model distress class '{raw_name}'. Allowed classes: {valid}."
    )
