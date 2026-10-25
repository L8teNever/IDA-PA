// Entry point: load start data, wire the shell and register every page URL.
import * as router from "./router.js";
import { loadBoot } from "./store.js";
import { initComposer } from "./ui/composer.js";
import { $, toast } from "./ui/dom.js";
import { initMascot } from "./ui/mascot.js";
import { initPwa } from "./ui/pwa.js";
import { initVoice } from "./ui/voice.js";
import { initSelects } from "./ui/select.js";
import { initShell, setView } from "./ui/shell.js";
import { openChat } from "./pages/chat.js";
import { chatsPage } from "./pages/chats.js";
import { agentPage, homePage, initHomeScroll } from "./pages/home.js";
import { inboxPage } from "./pages/inbox.js";
import { routineDetail, routineForm, routinesPage } from "./pages/routines.js";
import { settingsPage } from "./pages/settings/index.js";
import { usagePage } from "./pages/usage.js";

router.add("/", homePage);
router.add("/a/:id", agentPage);
router.add("/chat/:id", ({ id }) => openChat(id).catch(() => { toast("Chat nicht gefunden"); router.navigate("/chats", { replace: true }); }));
router.add("/chats", chatsPage);
router.add("/nachrichten", inboxPage);
router.add("/routinen", routinesPage);
router.add("/routinen/neu", () => routineForm());
router.add("/routinen/:id/bearbeiten", routineForm);
router.add("/routinen/:id", routineDetail);
router.add("/limits", usagePage);
router.add("/einstellungen", () => settingsPage());
router.add("/einstellungen/:section", settingsPage);
router.add("/einstellungen/:section/:id", settingsPage);
router.fallback(() => {
  setView("page");
  $("page").innerHTML = `<div class="soon-box" style="margin-top:40px"><span class="ms">explore_off</span>Diese Seite gibt es nicht.<br><br><a class="es-btn" href="/">Zur Startseite</a></div>`;
});

async function start() {
  await loadBoot();
  initSelects();
  initShell();
  initMascot();
  initPwa();
  initComposer();
  initVoice();
  initHomeScroll();
  await router.start();
}

start().catch((e) => { console.error(e); toast(e.message || "Fehler beim Laden"); });
