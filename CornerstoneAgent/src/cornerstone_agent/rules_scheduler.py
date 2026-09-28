"""A2 规则调度：按规则 interval（默认 4h）轮询评估并落盘。"""

from __future__ import annotations

import threading
import time
from typing import Any

from .bridge_client import BridgeClient
from .config import AgentConfig
from .jobs import handle_operator_notice
from .rules_audit import RulesAuditStore
from .rules_engine import RuleDef, evaluate_rules, load_rule_defs
from .ui_api import resolve_bridge_url


class RulesScheduler:
    """每个启用规则独立线程，按各自 ``interval_s`` 评估。"""

    def __init__(
        self,
        cfg: AgentConfig,
        audit: RulesAuditStore,
        *,
        registry=None,
        rules: list[RuleDef] | None = None,
    ):
        self.cfg = cfg
        self.audit = audit
        self.registry = registry
        if rules is not None:
            self.rules = list(rules)
        else:
            path = (cfg.rules.rules_path or "").strip()
            resolved = cfg.resolve_data_path(path, "rules/default.json") if path else None
            self.rules = load_rule_defs(resolved if resolved and resolved.is_file() else None)
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.last_ticks: dict[str, dict[str, Any]] = {}
        self._tick_lock = threading.Lock()

    def start(self) -> None:
        self._stop = threading.Event()
        self._threads = []
        if not self.cfg.rules.enabled:
            return
        for rule in self.rules:
            if not rule.enabled:
                continue
            t = threading.Thread(
                target=self._loop_rule,
                args=(rule.id,),
                name=f"rules-{rule.id}",
                daemon=True,
            )
            self._threads.append(t)
            t.start()

    def stop(self) -> None:
        self._stop.set()
        threads = list(self._threads)
        self._threads = []
        for t in threads:
            t.join(timeout=2.0)

    def reload(self, cfg: AgentConfig, rules: list[RuleDef] | None = None) -> None:
        self.stop()
        self.cfg = cfg
        if rules is not None:
            self.rules = list(rules)
        else:
            path = (cfg.rules.rules_path or "").strip()
            resolved = cfg.resolve_data_path(path, "rules/default.json") if path else None
            self.rules = load_rule_defs(resolved if resolved and resolved.is_file() else None)
        self.start()

    def _rule_by_id(self, rule_id: str) -> RuleDef | None:
        for r in self.rules:
            if r.id == rule_id:
                return r
        return None

    def _loop_rule(self, rule_id: str) -> None:
        rule = self._rule_by_id(rule_id)
        if rule is None:
            return
        # 启动后稍延迟，避免与时序/心跳同时打满 Bridge
        if self._stop.wait(2.0):
            return
        self._tick_rule(rule)
        interval = max(60.0, float(rule.interval_s))
        next_due = time.time() + interval
        while not self._stop.is_set():
            remain = next_due - time.time()
            if remain > 0:
                if self._stop.wait(min(1.0, remain)):
                    break
                continue
            started = time.time()
            rule = self._rule_by_id(rule_id)
            if rule is None or not rule.enabled:
                break
            self._tick_rule(rule)
            interval = max(60.0, float(rule.interval_s))
            next_due = started + interval
            if next_due < time.time():
                next_due = time.time()

    def _tick_rule(self, rule: RuleDef) -> None:
        try:
            self._tick_rule_inner(rule)
        except Exception as exc:  # noqa: BLE001
            print(f"[rules] {rule.id} tick failed: {exc}")

    def _tick_rule_inner(self, rule: RuleDef) -> None:
        cfg = self.cfg
        timeout = min(float(cfg.bridge.timeout_s), 90.0)
        results: list[dict[str, Any]] = []
        for ep in cfg.local_instruments():
            if self._stop.is_set():
                break
            url = ep.bridge_url
            if self.registry is not None:
                resolved = resolve_bridge_url(cfg, self.registry, ep.instrument_id)
                if resolved:
                    url = resolved
            bridge = BridgeClient(url, timeout_s=timeout)
            out = evaluate_rules(
                bridge,
                [rule],
                instrument_id=ep.instrument_id,
                lab_id=ep.lab_id,
            )
            # 仅在维护缺失（hit）或拉取失败时落盘；通过且无错误可记摘要便于排查
            should_record = bool(out.get("hit")) or bool(out.get("fetch_error"))
            if should_record or cfg.rules.record_ok:
                self.audit.record(
                    {
                        "event": "rules_eval",
                        "rule_id": rule.id,
                        "lab_id": ep.lab_id,
                        "instrument_id": ep.instrument_id,
                        "agent_id": ep.agent_id,
                        "hit": bool(out.get("hit")),
                        "suggestions": out.get("suggestions") or [],
                        "rule_results": out.get("rule_results") or [],
                        "fetch_error": out.get("fetch_error") or "",
                        "checked_at": out.get("checked_at") or "",
                        "duration_ms": out.get("duration_ms"),
                    }
                )
            if out.get("hit") and cfg.rules.post_notice:
                for sug in out.get("suggestions") or []:
                    try:
                        handle_operator_notice(
                            bridge,
                            {
                                "title": f"规则：{rule.id}",
                                "message": str(sug.get("message") or ""),
                                "severity": str(sug.get("severity") or rule.severity),
                                "source": "agent_rule",
                                "evidence": sug.get("evidence") or [],
                                "require_ack": True,
                            },
                            {
                                "lab_id": ep.lab_id,
                                "instrument_id": ep.instrument_id,
                                "agent_id": ep.agent_id,
                            },
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[rules] post_notice failed {ep.instrument_id}: {exc}")
            results.append(
                {
                    "instrument_id": ep.instrument_id,
                    "hit": out.get("hit"),
                    "expired_missing_count": (
                        (out.get("rule_results") or [{}])[0].get("expired_missing_count")
                        if out.get("rule_results")
                        else 0
                    ),
                    "fetch_error": out.get("fetch_error") or "",
                }
            )
            print(
                f"[rules] {rule.id}/{ep.instrument_id} hit={out.get('hit')} "
                f"err={out.get('fetch_error') or '-'}"
            )
        summary = {
            "rule_id": rule.id,
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "instruments": len(results),
            "hits": sum(1 for r in results if r.get("hit")),
            "interval_s": rule.interval_s,
            "results": results,
        }
        with self._tick_lock:
            self.last_ticks[rule.id] = summary
