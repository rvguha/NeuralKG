"""A stand-in for an instance's own connector code, used by the extension tests."""


def setup(registry):
    @registry.executor("demo_counter")
    async def run(frame, *, context):
        # a real one would query something; this proves the frame contract reaches instance code
        return {"value": len(frame.mention), "unit": "characters",
                "source": "demo extension", "ident": frame.ident}

    @registry.candidate_filter
    def only_public(candidates, principal):
        held = set((principal or {}).get("entitlements") or [])
        visible = [c for c in candidates if not c.get("private") or c.get("needs") in held]
        return visible, [c for c in candidates if c not in visible]

    @registry.principal
    def principal(request):
        return {"entitlements": list((request or {}).get("entitlements") or [])}
