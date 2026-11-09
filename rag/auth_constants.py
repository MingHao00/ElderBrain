"""登录、配额和会话的具名常量。调用处不再写裸数字。"""

# 绝对有效期 8 小时。不签发刷新令牌，到期重新登录。
SESSION_TTL_SECONDS = 8 * 60 * 60
# 校验两端允许的时钟偏差。
CLOCK_SKEW_SECONDS = 60
JWT_KEY_BYTES = 32
# 16 字节是 128 位，满足 jti 的下限。
JTI_BYTES = 16
JTI_HEX_CHARS = JTI_BYTES * 2
USER_ID_PREFIX = "u_"
USER_ID_RANDOM_BYTES = 16
USER_ID_MAX_CHARS = 64

TOKEN_ALG = "HS256"
TOKEN_TYP = "elderbrain-session+jwt"
TOKEN_ISS = "elderbrain"
TOKEN_AUD = "elderbrain-web"
ROLE_USER = "user"
ROLE_ADMIN = "admin"
PROVIDER_GITHUB = "github"
PROVIDER_EMAIL = "email"
FORBIDDEN_HEADERS = frozenset({"jku", "jwk", "x5u", "x5c"})
SESSION_CLAIMS = frozenset({"iss", "aud", "sub", "role", "iat", "nbf", "exp", "jti"})

SESSION_COOKIE = "__Host-eb_session"
CSRF_COOKIE = "__Host-eb_csrf"
# 入库前会转成小写。
CSRF_HEADER = "x-csrf-token"
CSRF_TOKEN_BYTES = 32

# 每日次数和全站费用按北京时间的自然日重置。
SHANGHAI_TZ = "Asia/Shanghai"
DAILY_LIMIT = 100
# 注册后这段时间用更低的每日上限。
NEW_ACCOUNT_WINDOW_SECONDS = 24 * 60 * 60
NEW_ACCOUNT_DAILY_LIMIT = 20
MINUTE_LIMIT = 10
MINUTE_WINDOW_SECONDS = 60
# 月模型预算约 650 元，按 30 天折合每天 21 元。
SITE_DAILY_CAP_YUAN = 21
MICRO_PER_YUAN = 1_000_000
SITE_DAILY_CAP_MICRO = SITE_DAILY_CAP_YUAN * MICRO_PER_YUAN
# 达到全站每日上限的这个百分比时告警一次。整数除法得到恰好 16.8 元。
SITE_DAILY_ALERT_PERCENT = 80
ALERT_PERCENT_SCALE = 100
SITE_DAILY_ALERT_MICRO = (
    SITE_DAILY_CAP_MICRO * SITE_DAILY_ALERT_PERCENT // ALERT_PERCENT_SCALE
)
COST_ALERT_SUBJECT = f"当日模型费用已达上限的 {SITE_DAILY_ALERT_PERCENT}%"
COST_ALERT_BODY = (
    f"全站当日模型费用已达到每日上限的 {SITE_DAILY_ALERT_PERCENT}%。"
)
# 预占整笔上限，因为输入 token 没有硬上限，小于上限的估价在并发下仍会超支。
ASK_COST_MICRO = SITE_DAILY_CAP_MICRO

# 同时有效的密钥上限。要新建必须先吊销，以后可以改这个数。
MAX_ACTIVE_API_KEYS = 2
API_KEY_PREFIX = "eb_"
API_KEY_RANDOM_BYTES = 32
API_KEY_ID_BYTES = 8

# 8 位足够人手输入，又不会一次就被猜中。
CODE_LENGTH = 8
CODE_TTL_SECONDS = 10 * 60
CODE_SALT_BYTES = 16
# 错满就作废，挡住对短码的在线穷举。
CODE_MAX_ATTEMPTS = 5
# 60 秒内不重发，减轻连点和对同一邮箱的轰炸。
EMAIL_RESEND_SECONDS = 60
# 每小时 5 封，挡住持续刷验证码。
EMAIL_HOURLY_CAP = 5
EMAIL_HOUR_SECONDS = 60 * 60
EMAIL_MAX_CHARS = 254
# 同一 IP 每小时最多 5 个新账号，挡住批量注册。
IP_HOURLY_SIGNUPS = 5
IP_HOUR_SECONDS = 60 * 60

# 只覆盖跳到 GitHub 再跳回来。过期的 state 不能复用。
OAUTH_STATE_TTL_SECONDS = 10 * 60
OAUTH_STATE_BYTES = 32
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_SCOPE = "read:user"
GITHUB_USER_AGENT = "ElderBrain"
HTTP_TIMEOUT_SECONDS = 10

SMTP_TIMEOUT_SECONDS = 10
SMTP_SSL_PORT = 465
SMTP_PORT_MIN = 1
SMTP_PORT_MAX = 65535
MAIL_SUBJECT = "ElderBrain 验证码"

SQLITE_TIMEOUT_SECONDS = 5
AUTH_DB_NAME = "auth.sqlite"

# 提问响应和审计共用这些状态码，避免两处各写一个数。
HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_UNPROCESSABLE = 422
HTTP_TOO_MANY = 429
HTTP_SERVER_ERROR = 500

PATH_ASK = "/api/ask"
PATH_QUERY = "/api/query"
PATH_CONVERSATIONS = "/api/conversations"
# 对话编号由服务端生成。客户端自己编的号对不上库里的行。
CONVERSATION_ID_PREFIX = "c_"
CONVERSATION_ID_BYTES = 16
PATH_SESSION = "/api/auth/session"
PATH_EMAIL_CODE = "/api/auth/email/code"
PATH_EMAIL_VERIFY = "/api/auth/email/verify"
PATH_LOGOUT = "/api/auth/logout"
PATH_GITHUB_START = "/api/auth/github/start"
PATH_GITHUB_CALLBACK = "/api/auth/github/callback"
PATH_KEYS = "/api/keys"
PATH_KEYS_REVOKE = "/api/keys/revoke"
PATH_ADMIN_SOURCE_DISABLE = "/api/admin/sources/disable"
PATH_ADMIN_SOURCE_ENABLE = "/api/admin/sources/enable"
PATH_ADMIN_INDEX_REBUILD = "/api/admin/index/rebuild"
PATH_ADMIN_INDEX_PUBLISH = "/api/admin/index/publish"
PATH_ADMIN_INDEX_ROLLBACK = "/api/admin/index/rollback"
PATH_ADMIN_REVIEW_QUEUE = "/api/admin/review-queue"

ERR_UNAUTHORIZED = "请重新登录，或检查密钥"
ERR_FORBIDDEN = "无权限"
ERR_ANSWER_FAILED = "回答失败"
ERR_JSON = "请求不是 JSON"
ERR_EMAIL = "邮箱不正确"
ERR_CODE = "验证码不正确"
ERR_CODE_LATER = "请稍后再获取验证码"
ERR_EMAIL_UNCONFIGURED = "邮箱登录未配置"
ERR_LOGIN_UNAVAILABLE = "登录暂不可用"
ERR_MAIL_UNCONFIGURED = "邮件发送未配置"
ERR_MAIL_FAILED = "邮件发送失败"
ERR_SIGNUP_LIMIT = "暂时无法注册"
ERR_GITHUB_UNCONFIGURED = "GitHub 登录未配置"
ERR_GITHUB_FAILED = "GitHub 登录失败"
ERR_KEY_LIMIT = "请先吊销一把密钥"
ERR_KEY_MISSING = "没有这把密钥"
ERR_NOT_FOUND = "没有这个接口"
ERR_SOURCE_MISSING = "没有这个来源"
ERR_INDEX_MISSING = "没有这个索引版本"
ERR_INDEX_UNCONFIGURED = "索引构建未配置"
ERR_INDEX_EXISTS = "索引版本已存在"
EMPTY_QUESTION = "请写下问题"
QUESTION_TOO_LONG = "问题太长"
UNPROCESSABLE_QUESTIONS = frozenset({EMPTY_QUESTION, QUESTION_TOO_LONG})

QUOTA_DAILY = "daily"
QUOTA_MINUTE = "minute"
QUOTA_SITE = "site"
# 提问占次数。闲置摘要只进当日费用，计次查询要跳过。
USE_KIND_ASK = "ask"
USE_KIND_IDLE = "idle"
# 未结算预占只计入全站上限。告警不能把这笔还没花出去的金额当成已经跨线。
QUOTA_UNSETTLED = 0
QUOTA_SETTLED = 1
SAFE_MAIL_ERRORS = frozenset({ERR_MAIL_UNCONFIGURED, ERR_MAIL_FAILED})
JSON_TYPE = "application/json; charset=utf-8"
# 网页在整段校验之后按行推送，不是模型 token 流。
NDJSON_TYPE = "application/x-ndjson; charset=utf-8"
TEXT_TYPE = "text/plain; charset=utf-8"
