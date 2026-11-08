import json
import unittest
from datetime import datetime, timedelta, timezone

from audit_record import (
    ADMIN_AUDIT_RETENTION_DAYS,
    CITATION_UNCHECKED,
    IP_HASH_RETENTION_DAYS,
    KIND_ADMIN_AUDIT,
    KIND_IP_HASH,
    KIND_REQUEST_AUDIT,
    REQUEST_AUDIT_RETENTION_DAYS,
    AuditRecordRejected,
    attach_feedback,
    audit_expires_at,
    build_admin_audit,
    build_request_audit,
    hash_client_ip,
)

CREATED = datetime(2026, 11, 6, 9, 0, tzinfo=timezone.utc)
# 文档示例网段，不是某台机器的地址。形状像令牌，但不是任何服务的密钥。
SAMPLE_IP = "192.0.2.15"
FAKE_TOKEN = "sk-placeholder-not-a-real-key"
CALLER_SALT = "caller-provided-salt"


def sample_request(**overrides: object) -> dict[str, object]:
    """拼一条合法请求，测试只改要考察的字段。"""
    payload: dict[str, object] = {
        "request_id": "req-1001",
        "user_id": "player-17",
        "created_at": CREATED,
        "question": "火球术的施法时间是多少？",
        "lang": "zh",
        "detected_version": "5.1",
        "result_type": "回答",
        "refusal_class": "",
        "retrieved": [{"entry_id": "spell-fireball", "similarity": 0.82}],
        "cited": ["spell-fireball"],
        "citation_check": "通过",
        "index_version": "src-srd51-zh-2026-11-06",
        "prompt_version": "answer-v1",
        "model_id": "qwen3.7-flash-2026-07-15",
        "guard_version": "guard-v1",
        "tokens_in": 120,
        "tokens_out": 40,
        "cost_estimate": 0.125,
        "latency_ms": {"检查": 15, "检索": 40, "首字": 80, "完整回答": 300},
    }
    payload.update(overrides)
    return build_request_audit(**payload)


class AuditRecordTests(unittest.TestCase):
    def test_legal_record_is_assembled(self) -> None:
        record = sample_request()
        self.assertEqual(
            set(record),
            {
                "request_id", "user_id", "created_at", "question", "lang",
                "detected_version", "result_type", "refusal_class", "retrieved",
                "cited", "citation_check", "index_version", "prompt_version",
                "model_id", "guard_version", "tokens_in", "tokens_out",
                "cost_estimate", "latency_ms", "feedback",
            },
        )
        self.assertEqual(record["request_id"], "req-1001")
        self.assertEqual(record["user_id"], "player-17")
        self.assertEqual(record["created_at"], CREATED.isoformat())
        self.assertEqual(record["result_type"], "回答")
        self.assertEqual(record["refusal_class"], "")
        self.assertEqual(
            record["retrieved"],
            [{"entry_id": "spell-fireball", "similarity": 0.82}],
        )
        self.assertEqual(record["cited"], ["spell-fireball"])
        self.assertEqual(record["citation_check"], "通过")
        self.assertEqual(
            record["latency_ms"],
            {"检查": 15, "检索": 40, "首字": 80, "完整回答": 300},
        )
        self.assertIsNone(record["feedback"])
        self.assertNotIn("client_ip_hash", record)
        admin = build_admin_audit("admin-3", CREATED, "发布")
        self.assertEqual(admin["actor_id"], "admin-3")
        self.assertEqual(admin["created_at"], CREATED.isoformat())
        self.assertEqual(admin["action"], "发布")

    def test_allowed_actions_and_citation_checks(self) -> None:
        for citation_check in ("通过", "重试后通过", "失败"):
            record = sample_request(citation_check=citation_check)
            self.assertEqual(record["citation_check"], citation_check)
        refused = sample_request(result_type="拒答", refusal_class="R2")
        self.assertEqual(refused["result_type"], "拒答")
        self.assertEqual(refused["refusal_class"], "R2")
        for action in ("接入来源", "停用来源", "发布", "回退", "调整配额"):
            admin = build_admin_audit("admin-3", CREATED, action)
            self.assertEqual(admin["action"], action)

    def test_email_user_id_is_rejected(self) -> None:
        with self.assertRaises(AuditRecordRejected):
            sample_request(user_id="person@example.com")

    def test_github_at_user_id_is_rejected(self) -> None:
        with self.assertRaises(AuditRecordRejected):
            sample_request(user_id="@example-user")
        with self.assertRaises(AuditRecordRejected):
            sample_request(index_version="https://github.com/example-user")

    def test_token_like_field_is_rejected(self) -> None:
        with self.assertRaises(AuditRecordRejected):
            sample_request(model_id=FAKE_TOKEN)

    def test_question_original_text_is_kept(self) -> None:
        question = f"请联系 person@example.com，样例 {FAKE_TOKEN}"
        record = sample_request(question=question)
        self.assertEqual(record["question"], question)

    def test_plaintext_ip_is_not_in_record(self) -> None:
        record = sample_request()
        dumped = json.dumps(record, ensure_ascii=False)
        self.assertNotIn("client_ip_hash", record)
        self.assertNotIn("client_ip_hash", dumped)
        self.assertNotIn(SAMPLE_IP, dumped)
        self.assertNotIn(CALLER_SALT, dumped)
        digest = hash_client_ip(SAMPLE_IP, CALLER_SALT)
        self.assertNotIn(SAMPLE_IP, digest)
        self.assertNotIn(CALLER_SALT, digest)
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(
            digest, hash_client_ip(SAMPLE_IP, "another-caller-salt"),
        )
        self.assertEqual(
            audit_expires_at(CREATED, KIND_IP_HASH),
            CREATED + timedelta(days=7),
        )

    def test_ip_hash_requires_caller_salt(self) -> None:
        with self.assertRaises(AuditRecordRejected):
            hash_client_ip(SAMPLE_IP, "")

    def test_illegal_admin_action_is_rejected(self) -> None:
        with self.assertRaises(AuditRecordRejected):
            build_admin_audit("admin-3", CREATED, "删除数据")

    def test_unchecked_citation_is_allowed(self) -> None:
        record = sample_request(citation_check=CITATION_UNCHECKED)
        self.assertEqual(record["citation_check"], CITATION_UNCHECKED)

    def test_refusal_without_class_is_allowed(self) -> None:
        record = sample_request(result_type="拒答", refusal_class="")
        self.assertEqual(record["result_type"], "拒答")
        self.assertEqual(record["refusal_class"], "")

    def test_illegal_citation_check_is_rejected(self) -> None:
        with self.assertRaises(AuditRecordRejected):
            sample_request(citation_check="跳过")

    def test_feedback_can_be_filled_later(self) -> None:
        original = sample_request()
        self.assertIsNone(original["feedback"])
        self.assertIsNone(sample_request(feedback={})["feedback"])
        updated = attach_feedback(original, {"rating": "好评", "note": ""})
        self.assertIsNone(original["feedback"])
        self.assertEqual(updated["feedback"], {"rating": "好评", "note": ""})
        with self.assertRaises(AuditRecordRejected):
            attach_feedback(original, {"rating": "差评", "note": "person@example.com"})

    def test_expiry_follows_retention_days(self) -> None:
        self.assertEqual(REQUEST_AUDIT_RETENTION_DAYS, 90)
        self.assertEqual(ADMIN_AUDIT_RETENTION_DAYS, 365)
        self.assertEqual(IP_HASH_RETENTION_DAYS, 7)
        self.assertEqual(
            audit_expires_at(CREATED, KIND_REQUEST_AUDIT),
            CREATED + timedelta(days=REQUEST_AUDIT_RETENTION_DAYS),
        )
        self.assertEqual(
            audit_expires_at(CREATED, KIND_ADMIN_AUDIT),
            CREATED + timedelta(days=ADMIN_AUDIT_RETENTION_DAYS),
        )
        self.assertEqual(
            audit_expires_at(CREATED, KIND_IP_HASH),
            CREATED + timedelta(days=IP_HASH_RETENTION_DAYS),
        )


if __name__ == "__main__":
    unittest.main()
