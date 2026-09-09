'''Post-generation validation and advisory safety checks.'''

import math
import re

from app.guardrails.contracts import GuardrailContext, GuardrailDecision


class GuardrailService:
    unsafe_patterns = (
        r'\bkubectl\s+delete\b',
        r'\bdrop\s+database\b',
        r'\btruncate\s+table\b',
        r'\brm\s+-rf\b',
        r'\bdelete\s+production\b',
    )

    def validate(self, context: GuardrailContext) -> GuardrailDecision:
        result = context.proposed_resolution
        if not math.isfinite(result.confidence) or not 0 <= result.confidence <= 1:
            return GuardrailDecision(accepted=False, limitation='confidence was outside the safe range')
        allowed_ids = set(context.allowed_evidence_ids)
        if any(item.id not in allowed_ids for item in result.evidence):
            return GuardrailDecision(accepted=False, limitation='result referenced evidence outside the selected bundle')
        for action in result.recommended_actions:
            if any(identifier not in allowed_ids for identifier in action.evidence_ids):
                return GuardrailDecision(accepted=False, limitation='recommendation referenced evidence outside the selected bundle')
            if any(re.search(pattern, action.text, re.IGNORECASE) for pattern in self.unsafe_patterns):
                return GuardrailDecision(accepted=False, limitation='recommendation contained a destructive command')
            if not action.requires_confirmation:
                return GuardrailDecision(accepted=False, limitation='operational actions require operator confirmation')
        if not result.recommended_actions:
            return GuardrailDecision(accepted=False, limitation='recommendation was empty')
        return GuardrailDecision(accepted=True, result=result)
