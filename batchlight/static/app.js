// Small progressive enhancements. Every page works without this file.
document.addEventListener("submit", (event) => {
  const form = event.target;
  const message = form.getAttribute("data-confirm");
  if (message && !window.confirm(message)) event.preventDefault();
});
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) return;
  const target = document.getElementById(button.getAttribute("data-copy"));
  if (!target || !navigator.clipboard) return;
  navigator.clipboard.writeText(target.value || target.textContent.trim()).then(() => {
    const original = button.textContent;
    button.textContent = "Copied";
    setTimeout(() => { button.textContent = original; }, 1500);
  });
});
document.addEventListener("change", (event) => {
  if (event.target.matches("[data-autosubmit]")) event.target.form.submit();
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  for (const menu of document.querySelectorAll("details[data-menu][open]")) {
    menu.open = false;
    menu.querySelector("summary").focus();
  }
});
