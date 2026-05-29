// 帝云前端工具函式

// POST 表單請求，自動攜帶 Session Cookie
async function postForm(url, data) {
  const params = new URLSearchParams();
  Object.entries(data).forEach(([k, v]) => params.append(k, v));

  return fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded"
    },
    credentials: "include",
    body: params
  });
}

// 彈窗提示
function toast(msg, isSuccess = false) {
  const el = document.createElement("div");
  el.className = `fixed top-4 right-4 px-4 py-2 rounded z-50 ${
    isSuccess ? "bg-green-600" : "bg-red-600"
  } text-white text-sm shadow-lg`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// 倒數計時（發送驗證碼按鈕）
function startCountdown(btn, seconds = 60) {
  btn.disabled = true;
  const timer = setInterval(() => {
    seconds--;
    btn.textContent = `${seconds} 秒後重發`;
    if (seconds <= 0) {
      clearInterval(timer);
      btn.textContent = "發送驗證碼";
      btn.disabled = false;
    }
  }, 1000);
}
