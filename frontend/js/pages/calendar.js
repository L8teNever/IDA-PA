// "/kalender" – der IDA Kalender (eigener Dienst) im Rahmen von IDA; derselbe Stil, Anfragen laufen über den Proxy /kal/.
import { mainAgent } from "../store.js";
import { $ } from "../ui/dom.js";
import { setAgent, setView } from "../ui/shell.js";

export async function calendarPage() {
  setAgent(mainAgent());
  setView("page");
  $("page").innerHTML = `<iframe class="cal-frame" src="/kal/" title="Kalender" allow="clipboard-write"></iframe>`;
}
