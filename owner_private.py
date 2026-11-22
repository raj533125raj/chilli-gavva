"""Explicit private-owner text; public exports always retain their own amount filter."""

class OwnerPrivateText(str):
    """Carry the sales/report privacy scope through harmless burst-note concatenation."""
    def __add__(self, other):
        return OwnerPrivateText(super().__add__(other))

    def __radd__(self, other):
        return OwnerPrivateText(str(other) + str(self))
