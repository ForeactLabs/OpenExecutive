// Asks Chrome for the optional "debugger" permission that texts need to
// press Send in Google Messages. Opened once when Texts is turned on.
const $ = (id) => document.getElementById(id);

async function show() {
  const ok = await chrome.permissions.contains({ permissions: ["debugger"] }).catch(() => false);
  $("allow").hidden = ok;
  $("done").hidden = !ok;
  return ok;
}

$("allow").addEventListener("click", async () => {
  const got = await chrome.permissions.request({ permissions: ["debugger"] }).catch(() => false);
  $("no").hidden = got === true;
  if (await show()) setTimeout(() => window.close(), 1500);
});

void show();
