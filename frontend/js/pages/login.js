// "/login"
import { api } from "../api.js";
import { $ } from "../ui/dom.js";
import { setView } from "../ui/shell.js";

export function loginPage(_, query) {
  setView("login");
  $("page").innerHTML = `
    <div class="login-card">
      <div class="wiz-prev"><div class="face-p" style="background:radial-gradient(120% 100% at 50% 25%, #fff 30%, #dfe7f1 100%);box-shadow:0 0 30px rgba(60,170,220,.35)"><i></i><i></i></div></div>
      <h1>IDA</h1>
      <form id="loginForm">
        <label class="fld"><span>Passwort</span><input id="pw" type="password" autocomplete="current-password" autofocus></label>
        <div id="loginErr" class="ferr"></div>
        <button class="es-btn primary" style="width:100%"><span class="ms">login</span>Anmelden</button>
      </form>
    </div>`;
  $("loginForm").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api.post("/auth/login", { password: $("pw").value }, { allow401: true });
      location.href = query.next && query.next.startsWith("/") && !query.next.startsWith("//") ? query.next : "/";
    } catch (err) { $("loginErr").textContent = err.message; }
  };
}
