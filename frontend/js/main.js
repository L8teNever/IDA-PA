// Entry point: load start data, wire the shell and register every page URL.
import * as router from "./router.js";
import { loadBoot } from "./store.js";
import { initComposer } from "./ui/composer.js";
import { $, toast } from "./ui/dom.js";
import { initMascot } from "./ui/mascot.js";
import { initPwa } from "./ui/pwa.js";
import { initVoice } from "./ui/voice.js";
import { initBack } from "./ui/back.js";
import { initSelects } from "./ui/select.js";
import { initShell, setView } from "./ui/shell.js";
import { openChat } from "./pages/chat.js";
import { chatsPage } from "./pages/chats.js";
import { agentPage, homePage, initHomeScroll } from "./pages/home.js";
import { inboxPage } from "./pages/inbox.js";
import { routineDetail, routineForm, routinesPage } from "./pages/routines.js";
import { settingsPage } from "./pages/settings/index.js";
import { usagePage } from "./pages/usage.js";
import { remindersPage } from "./pages/reminders.js";
import { codePage, projectPage, threadPage } from "./pages/code.js";
import { trackerPage } from "./pages/tracker.js";
import { kalenderUrl } from "./store.js";
import { tapPage, timerPage } from "./pages/tap.js";

router.add("/", homePage);
router.add("/a/:id", agentPage);
router.add("/chat/:id", ({ id }) => openChat(id).catch(() => { toast("Chat nicht gefunden"); router.navigate("/chats", { replace: true }); }));
router.add("/chats", chatsPage);
router.add("/nachrichten", inboxPage);
router.add("/routinen", routinesPage);
router.add("/erinnerungen", remindersPage);
router.add("/erinnerungen/:id", remindersPage); // also /erinnerungen/neu
router.add("/routinen/neu", () => routineForm());
router.add("/routinen/:id/bearbeiten", routineForm);
router.add("/routinen/:id", routineDetail);
// der Kalender ist eine eigene App (kalender.kulbarts.com); alte Links nach /kalender führen dorthin
router.add("/kalender", () => location.replace(kalenderUrl() || "/"));
router.add("/kalender/termin/:id", ({ id }) => location.replace(kalenderUrl() ? `${kalenderUrl()}/termin/${id}` : "/"));
router.add("/tracker", trackerPage);
router.add("/tracker/:id/timer", timerPage);
router.add("/tracker/:id", trackerPage); // also /tracker/neu
router.add("/t/:token", tapPage);
router.add("/code", codePage);
router.add("/code/:project", projectPage);
router.add("/code/:project/:thread", threadPage); // also /code/:project/neu
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
  initBack();
  initHomeScroll();
  await router.start();
}

start().catch((e) => { console.error(e); toast(e.message || "Fehler beim Laden"); });
