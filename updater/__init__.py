"""Centralized offline-data chunk manager (plans/data-update-plan.md).

The declarative registry of every offline data chunk (``updater.chunks``) and
the derived-freshness probes behind it (``updater.probes``). Freshness is
always computed from the data itself at read time — never stored — so it
cannot drift when an importer runs outside the system (e.g. over SSH).
"""
