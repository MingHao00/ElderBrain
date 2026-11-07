const SESSION_PATH = "/api/auth/session";
const CODE_PATH = "/api/auth/email/code";
const VERIFY_PATH = "/api/auth/email/verify";
const LOGOUT_PATH = "/api/auth/logout";
const CSRF_NAME = "__Host-eb_csrf=";
const LOGIN_FAILED = "登录没有完成。";
const CODE_SENT = "验证码已发出";

const loggedOut = document.getElementById("loggedOut");
const loggedIn = document.getElementById("loggedIn");
const loginStatus = document.getElementById("loginStatus");
const loginEmail = document.getElementById("loginEmail");
const loginCode = document.getElementById("loginCode");
const accountRole = document.getElementById("accountRole");

let authBusy = false;

window.elderbrainCsrf = readCsrf;
document.getElementById("sendCodeButton").addEventListener("click", sendCode);
document.getElementById("verifyCodeButton").addEventListener("click", verifyCode);
document.getElementById("logoutButton").addEventListener("click", logout);
loginEmail.addEventListener("keydown", onEnter(sendCode));
loginCode.addEventListener("keydown", onEnter(verifyCode));
refreshSession();

function readCsrf() {
  const parts = document.cookie.split(";");
  for (let index = 0; index < parts.length; index += 1) {
    const item = parts[index].trim();
    if (item.indexOf(CSRF_NAME) !== 0) {
      continue;
    }
    return decodeCookie(item.slice(CSRF_NAME.length));
  }
  return "";
}

function decodeCookie(value) {
  try {
    return decodeURIComponent(value);
  } catch (error) {
    return value;
  }
}

function onEnter(action) {
  return function (event) {
    if (event.key !== "Enter") {
      return;
    }
    event.preventDefault();
    action();
  };
}

function refreshSession() {
  fetch(SESSION_PATH, {credentials: "same-origin"})
    .then(function (response) {
      return response.json();
    })
    .then(applySession)
    .catch(function () {
      applySession({logged_in: false});
    });
}

function sendCode() {
  postAuth(CODE_PATH, {email: loginEmail.value}, function (result) {
    if (result.ok) {
      showStatus(CODE_SENT);
      return;
    }
    showStatus(errorText(result));
  });
}

function verifyCode() {
  postAuth(VERIFY_PATH, {email: loginEmail.value, code: loginCode.value}, function (result) {
    if (!result.ok) {
      showStatus(errorText(result));
      return;
    }
    applySession(result.body);
    showStatus("");
  });
}

function logout() {
  postAuth(LOGOUT_PATH, {}, function (result) {
    // 200 表示拒绝名单已写完。这个接口的 401 会带上清除 Cookie。
    // 500 时名单没写完，Cookie 还在，不能把界面收成已登出。
    if (result.status === 200 || result.status === 401) {
      applySession({logged_in: false});
      showStatus("");
      return;
    }
    showStatus(errorText(result));
  });
}

function postAuth(path, payload, done) {
  if (authBusy) {
    return;
  }
  authBusy = true;
  fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": readCsrf(),
    },
    body: JSON.stringify(payload),
  }).then(function (response) {
    return response.json().then(function (body) {
      return {ok: response.ok, status: response.status, body: body};
    });
  }).then(function (result) {
    authBusy = false;
    done(result);
  }).catch(function () {
    authBusy = false;
    showStatus(LOGIN_FAILED);
  });
}

function applySession(payload) {
  const logged = Boolean(payload && payload.logged_in === true);
  loggedOut.hidden = logged;
  loggedIn.hidden = !logged;
  if (!logged) {
    return;
  }
  loginEmail.value = "";
  loginCode.value = "";
  accountRole.textContent = payload.role === "admin" ? "管理员" : "用户";
}

function showStatus(text) {
  loginStatus.textContent = text;
}

function errorText(result) {
  const body = result.body || {};
  return typeof body.error === "string" ? body.error : LOGIN_FAILED;
}
