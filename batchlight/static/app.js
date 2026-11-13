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
// Community: photo preview before upload.
document.addEventListener("change", (event) => {
  const input = event.target.closest("input[type=file][data-preview]");
  if (!input || !input.files || !input.files[0]) return;
  const img = document.getElementById(input.getAttribute("data-preview"));
  if (!img) return;
  img.src = URL.createObjectURL(input.files[0]);
  img.classList.remove("hidden");
});
// Community: fill latitude and longitude from the device's location.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-geolocate]");
  if (!button || !navigator.geolocation) return;
  const original = button.textContent;
  button.textContent = "Finding you…";
  navigator.geolocation.getCurrentPosition((pos) => {
    const form = button.form;
    form.elements.latitude.value = pos.coords.latitude.toFixed(6);
    form.elements.longitude.value = pos.coords.longitude.toFixed(6);
    if (form.elements.location_source) form.elements.location_source.value = "device";
    button.textContent = "Location added";
  }, () => { button.textContent = original; alert("Your location could not be read. Please type the coordinates."); }, { enableHighAccuracy: true, timeout: 15000 });
});
// Community: share a post with the system share sheet, or copy its link.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-share]");
  if (!button) return;
  const url = button.getAttribute("data-share");
  const title = button.getAttribute("data-title") || document.title;
  const count = () => {
    const form = button.closest("form");
    if (form) fetch(form.action, { method: "POST", body: new FormData(form), credentials: "same-origin" }).catch(() => {});
  };
  event.preventDefault();
  if (navigator.share) {
    navigator.share({ title, url }).then(count).catch(() => {});
  } else if (navigator.clipboard) {
    navigator.clipboard.writeText(url).then(() => {
      count();
      const original = button.textContent;
      button.textContent = "Link copied";
      setTimeout(() => { button.textContent = original; }, 1500);
    });
  }
});
