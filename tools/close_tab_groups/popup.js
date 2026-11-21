// Lists every tab group in every Chrome window, and closes them all on one click.
// Tabs outside groups are never touched.
async function load() {
  const groups = await chrome.tabGroups.query({});
  const tabs = await chrome.tabs.query({});
  const wins = new Set(tabs.map(t => t.windowId)).size;
  const grouped = tabs.filter(t => t.groupId !== -1);
  const list = document.getElementById("list");
  list.textContent = "";
  for (const g of groups) {
    const n = grouped.filter(t => t.groupId === g.id).length;
    const li = document.createElement("li");
    li.textContent = `${g.title || "(no name)"} - ${n} tab${n === 1 ? "" : "s"}`;
    list.appendChild(li);
  }
  document.getElementById("head").textContent =
    groups.length ? `${groups.length} groups, ${grouped.length} tabs` : "No tab groups open";
  document.getElementById("note").textContent =
    `I can see ${wins} window${wins === 1 ? "" : "s"} and ${tabs.length} tabs in this Chrome profile. ` +
    "Tabs that are not in a group stay open. Group labels on the bookmarks bar with no open tabs are saved groups: I can't see or delete those.";
  const go = document.getElementById("go");
  go.disabled = grouped.length === 0;
  go.onclick = async () => {
    go.disabled = true;
    try {
      // If every tab is in a group, open a blank one first so Chrome itself stays open.
      if (grouped.length === tabs.length) await chrome.tabs.create({ active: false });
      await chrome.tabs.remove(grouped.map(t => t.id));
      await load();
    } catch (e) { showError(e); }
  };
}
function showError(e) {
  document.getElementById("head").textContent = "Error: " + (e && e.message ? e.message : e);
}
load().catch(showError);
