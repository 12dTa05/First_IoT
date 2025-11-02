/* ==== Tabs ==== */

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", (e) => {
    e.preventDefault();
    const name = tab.dataset.tab;

    if (name === "logout") {
      handleLogout();
      return; // ⛔ Dừng, không chạy tiếp các dòng bên dưới
    }
    document
      .querySelectorAll(".tab")
      .forEach((t) => t.classList.remove("active"));
    document
      .querySelectorAll("section")
      .forEach((s) => s.classList.remove("active"));

    tab.classList.add("active");
    document.getElementById("tab-" + name).classList.add("active");

    if (name === "fan") loadFan();
    else if (name === "notify") loadFeed();
    else if (name === "rfid") loadRfidDevice();
    else if (name === "dashboard") {
      loadTemperatureChart();
      // loadFanChart();
    } else if (name === "history") {
      console.log("[DEBUG] Tab 'Lịch sử vào/ra' được click → loadHistory()");
      loadHistory();
    } else if (name === "logout") {
      handleLogout();
    }
  });
});

/* ==== USER ID ==== */
function getCurrentUserId() {
  return window.currentUserId || localStorage.getItem("currentUserId") || null;
}

/* ==== Toast ==== */
function showToast(ok, msg) {
  const el = document.getElementById("toast");
  el.classList.remove("show", "ok", "err");
  el.offsetHeight; // ⚡️force reflow để reset animation

  el.classList.add(ok ? "ok" : "err");
  el.textContent = msg;

  requestAnimationFrame(() => {
    el.classList.add("show");
    setTimeout(() => {
      el.classList.remove("show");
    }, 1600);
  });
}
/* ==== INIT SAU LOGIN ==== */

async function initAfterLogin(user_id) {
  console.log("🚀 InitAfterLogin:", user_id);

  console.log("[DEBUG] 1 - Fetch thiết bị...");
  const [fanRes, rfidRes, passRes] = await Promise.all([
    fetch(`/access/get_device?user_id=${user_id}&device_type=fan controller`),
    fetch(`/access/get_device?user_id=${user_id}&device_type=rfid_gate`),
    fetch(`/access/get_device?user_id=${user_id}&device_type=passkey`),
  ]);

  console.log("[DEBUG] 2 - Parse JSON...");
  const [fanJs, rfidJs, passJs] = await Promise.all([
    fanRes.json(),
    rfidRes.json(),
    passRes.json(),
  ]);

  console.log("[DEBUG] 3 - Gán biến thiết bị...");
  if (fanJs.ok && fanJs.found) {
    window.currentFanDevice = fanJs.device_id;
    window.currentFanGateway = fanJs.gateway_id;
    console.log("🌀 Fan:", fanJs.device_id);
  }
  if (rfidJs.ok && rfidJs.found) {
    window.currentRfidDevice = rfidJs.device_id;
    window.currentRfidGateway = rfidJs.gateway_id;
    console.log("📡 RFID:", rfidJs.device_id);
  }
  if (passJs.ok && passJs.found) {
    window.currentPassDevice = passJs.device_id;
    window.currentPassGateway = passJs.gateway_id;
    console.log("🔑 Passkey:", passJs.device_id);
  }
  // 🔹 Load các thành phần
  try {
    await Promise.all([loadFan(), loadRfidDevice(), loadPasskeyDevice()]);
  } catch (e) {
    console.error("[ERROR] Khi tải thiết bị:", e);
  }

  try {
    console.log("[DEBUG] 4 - Gọi loadTemperature...");
    // await loadTemperature();
    await loadTemperatureChart();
  } catch (e) {
    console.error("[ERROR] loadTemperature:", e);
  }

  try {
    console.log("[DEBUG] 5 - Gọi loadHistory...");
    await loadHistory();
  } catch (e) {
    console.error("[ERROR] loadHistory:", e);
  }

  try {
    console.log("[DEBUG] 6 - Gọi loadFeed...");
    await loadFeed();
  } catch (e) {
    console.error("[ERROR] loadFeed:", e);
  }
}

/* ==== HIỂN THỊ THÔNG BÁO TRẠNG THÁI THIẾT BỊ ==== */
function showDeviceMessage(msgId, text, type = "error") {
  const msg = document.getElementById(msgId);
  if (!msg) {
    console.warn(`⚠️ Element #${msgId} not found`);
    return;
  }
  msg.textContent = text;
  msg.style.background =
    type === "error"
      ? "rgba(220,0,0,0.85)"
      : type === "success"
      ? "rgba(0,150,0,0.85)"
      : "rgba(0,0,0,0.85)";
  msg.classList.remove("show");
  void msg.offsetWidth;
  msg.classList.add("show");
}

/* ==== LOGIN ==== */
let LOGGED_USER = null;
let USER_ROLE = null;

async function submitLogin() {
  const username = document.getElementById("login_user").value.trim();
  const password = document.getElementById("login_pass").value.trim();
  if (!username || !password) {
    document.getElementById("login_hint").textContent = "Chưa nhập đầy đủ";
    return;
  }

  try {
    const res = await fetch("/access/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const js = await res.json();
    if (js.ok) {
      LOGGED_USER = js.user_id;
      USER_ROLE = js.role;
      document.getElementById("login_backdrop").classList.remove("show-modal");
      window.currentUserId = js.user_id;
      // updateUIByRole();
      localStorage.setItem("currentUserId", js.user_id);

      showToast(true, `Xin chào ${js.full_name || js.username}`);
      await initAfterLogin(js.user_id);
      // Load riêng Passkey và RFID cho UI (song song để không delay)
      loadPasskeyDevice();
      loadRfidDevice();
    } else {
      document.getElementById("login_hint").textContent =
        "Sai tài khoản hoặc mật khẩu";
    }
  } catch (e) {
    document.getElementById("login_hint").textContent = "Lỗi mạng";
  }
}

/* ==== LOGOUT ==== */
function handleLogout() {
  // localStorage.removeItem("currentUserId");
  window.location.reload(); // 🔥 reload toàn bộ, reset mọi listener
}

/* ==== KIỂM TRA QUYỀN TRUY CẬP THIẾT BỊ ==== */

async function checkDevicePermission(user_id, device_id, section_id) {
  // 🔹 thêm dòng này để tránh undefined
  window.userPermissions = window.userPermissions || {};

  const cached = window.userPermissions?.[device_id];
  if (cached === false) {
    showDeviceMessage(
      `${section_id}_msg`,
      "🔒 Bạn không có quyền truy cập thiết bị này",
      "error"
    );
    document
      .querySelector(`#tab-${section_id}`)
      .classList.add("device-disabled");
    return false;
  }

  // sau đó mới fetch thật
  try {
    const r = await fetch(
      `/access/check_permission?user_id=${user_id}&device_id=${device_id}`
    );
    const js = await r.json();
    const ok = js.ok && js.granted;
    window.userPermissions[device_id] = ok;

    if (!ok)
      showDeviceMessage(
        `${section_id}_msg`,
        "🔒 Bạn không có quyền truy cập thiết bị này",
        "error"
      );
    return ok;
  } catch {
    showDeviceMessage(`${section_id}_msg`, "📡 Lỗi kiểm tra quyền", "error");
    return false;
  }
}
/* ==== FAN CONTROL ==== */
function setToggle(on) {
  const t = document.getElementById("toggler");
  const label = document.getElementById("fan_label");
  if (on) {
    t.classList.add("on");
    label.textContent = "On";
  } else {
    t.classList.remove("on");
    label.textContent = "Off";
  }
}

// 🔹 Load trạng thái ban đầu của quạt
async function loadFan() {
  const user_id = getCurrentUserId();
  const dev = window.currentFanDevice;
  const gw = window.currentFanGateway;
  const card = document.querySelector("#tab-fan .card");

  if (!dev || !gw) {
    card.classList.add("device-disabled");
    // showFanMessage("🔒 Bạn không có quyền truy cập thiết bị này", "error");
    showDeviceMessage("fan_msg", "🔒 Bạn không có quyền truy cập", "error");
    return;
  }

  const granted = await checkDevicePermission(user_id, dev);
  if (!granted) {
    card.classList.add("device-disabled");
    // showFanMessage("🔒 Bạn không có quyền truy cập thiết bị này", "error");
    showDeviceMessage("fan_msg", "🔒 Bạn không có quyền truy cập", "error");
    return;
  }

  try {
    const r = await fetch(`/fan/${gw}/${dev}/state`);
    const js = await r.json();
    if (!r.ok || !js.ok) throw new Error("state load failed");

    setToggle(js.status === "on");
    card.classList.remove("device-disabled");
  } catch (err) {
    console.error(err);
    showDeviceMessage("fan_msg", "📡 Lỗi tải trạng thái quạt", "error");
  }
}

// 🔹 Bật / Tắt quạt
async function toggleFan() {
  const dev = window.currentFanDevice;
  const gateway = window.currentFanGateway;
  const user_id = getCurrentUserId();

  if (!user_id) {
    showToast(false, "⚠️ Bạn chưa đăng nhập");
    return;
  }
  if (!dev || !gateway) {
    showToast(false, "⚙️ Không tìm thấy thiết bị hoặc gateway hiện tại");
    return;
  }

  const isOn = document.getElementById("toggler").classList.contains("on");
  const next = !isOn;
  setToggle(next); // cập nhật giao diện trước cho mượt

  try {
    const res = await fetch(`/fan/${gateway}/${dev}/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id }),
    });

    const js = await res.json();
    if (!js.ok) throw new Error(js.error);
    setToggle(js.state === "on");
    showToast(true, `💨 Quạt ${dev}: ${js.state.toUpperCase()}`);
  } catch (e) {
    // Nếu lỗi, revert lại trạng thái
    setToggle(isOn);
    console.error(e);
    showToast(false, "❌ Lỗi gửi lệnh bật/tắt quạt");
  }
}

/* ==== TEMPERATURE DASHBOARD ==== */
let tempChart, fanChart;

let tempChartObj = null;

async function loadTemperatureChart() {
  const user_id = getCurrentUserId();
  const tempDev = Object.keys(window.currentDevices || {}).find((k) =>
    k.toLowerCase().includes("temp")
  );

  try {
    const r = await fetch(`/dashboard/temperature?user_id=${user_id}`);
    const js = await r.json();
    if (!js.ok) return;

    // 🌤️ Hiển thị thông tin hiện tại
    const latest = js.latest;
    document.getElementById("temp_value").textContent =
      latest.temperature.toFixed(1) + "°C";
    document.getElementById("hum_value").textContent =
      latest.humidity.toFixed(1) + "%";
    document.getElementById("temp_time").textContent =
      "Lần đo: " + new Date(latest.time).toLocaleString("vi-VN");
    document.getElementById("location_info").textContent =
      "Thiết bị: " + js.device_id;
    document.getElementById("weather_icon").textContent = latest.icon || "🌡️";

    // 📊 Chuẩn bị dữ liệu biểu đồ
    const labels = js.chart.map((p) =>
      new Date(p.time).toLocaleTimeString("vi-VN", {
        hour: "2-digit",
        minute: "2-digit",
      })
    );
    const temps = js.chart.map((p) => p.temp);
    const hums = js.chart.map((p) => p.hum);

    const ctx = document.getElementById("tempChart").getContext("2d");
    if (tempChartObj) tempChartObj.destroy();

    tempChartObj = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "🌡️ Nhiệt độ (°C)",
            data: temps,
            borderColor: "rgba(255, 99, 132, 1)",
            backgroundColor: "rgba(255, 99, 132, 0.2)",
            fill: true,
            tension: 0.4,
          },
          {
            label: "💧 Độ ẩm (%)",
            data: hums,
            borderColor: "rgba(54, 162, 235, 1)",
            backgroundColor: "rgba(54, 162, 235, 0.2)",
            fill: true,
            tension: 0.4,
          },
        ],
      },
      options: {
        responsive: true,
        plugins: { legend: { position: "bottom" } },
        scales: {
          x: { ticks: { maxTicksLimit: 8 } },
          y: { beginAtZero: false },
        },
      },
    });
  } catch (err) {
    console.error("Lỗi tải biểu đồ:", err);
  }
}

/* ==== PASSKEY ==== */
let passBuf = "";
const placeholderChar = "•";

function renderScreen() {
  const screen = document.getElementById("screen");
  if (passBuf.length) {
    screen.textContent = "•".repeat(passBuf.length).padEnd(6, "·");
  } else {
    screen.textContent = "·".repeat(6);
  }
}

function tap(d) {
  if (passBuf.length < 6) {
    // ✅ chỉ cho phép tối đa 6 số
    passBuf += d;
    renderScreen();
  }
}

function delKey() {
  passBuf = passBuf.slice(0, -1);
  renderScreen();
}

function clearKey() {
  passBuf = "";
  renderScreen();
}

async function submitPasscode() {
  const dev = window.currentPassDevice;
  const gw = window.currentPassGateway;
  const user_id = getCurrentUserId(); // 🧩 thêm dòng này
  const inline = document.getElementById("pass_inline");

  if (!dev || !gw) {
    showToast(false, "Không tìm thấy thiết bị hiện tại");
    return;
  }

  if (passBuf.length !== 6) {
    showToast(false, "Passkey phải đủ 6 chữ số");
    return;
  }

  const toSend = passBuf;
  console.log(`[DEBUG] Send to /access/${gw}/${dev}/passcode`);

  try {
    const r = await fetch(`/access/${gw}/${dev}/passcode`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passcode: toSend, user_id }), // ✅ gửi kèm user_id
    });
    const js = await r.json();

    if (js.ok && js.result === "granted") {
      showToast(true, "✅ Mở cửa: GRANTED");
      inline.className = "inline-status ok";
      inline.textContent = "GRANTED";
    } else {
      showToast(false, "❌ Từ chối: DENIED");
      inline.className = "inline-status err";
      inline.textContent = "DENIED";
    }
  } catch (e) {
    console.error(e);
    showToast(false, "Lỗi mạng");
    inline.className = "inline-status err";
    inline.textContent = "NETWORK";
  }

  passBuf = "";
  renderScreen();
}

/* ==== PASSKEY MANAGEMENT ==== */
function openManageModal() {
  document.getElementById("manage_full").classList.add("show-modal");
  loadPasskeyList();
}

function closeManageModal() {
  document.getElementById("manage_full").classList.remove("show-modal");
}

async function loadPasskeyList() {
  try {
    const r = await fetch("/access/manage_passkey", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "list" }),
    });
    const js = await r.json();
    const box = document.getElementById("passkey_list");
    box.innerHTML = "";

    if (!js.ok || !Array.isArray(js.passwords)) {
      box.innerHTML = '<div class="muted">Không tải được danh sách</div>';
      return;
    }

    if (js.passwords.length === 0) {
      box.innerHTML = '<div class="muted">Chưa có passkey</div>';
      return;
    }

    for (const p of js.passwords) {
      const row = document.createElement("div");
      row.className = "listrow";
      row.innerHTML = `
        <div class="rowleft">
          <b>${p.id}</b> 
          <span class="muted" style="font-size:12px">${p.owner || "-"}</span>
        </div>
        <div class="rowright">
          <button class="btn-mini" onclick="editPasskey('${p.id}')">Sửa</button>
          <button class="btn-mini alt" onclick="confirmDeletePasskey('${
            p.id
          }')">Xoá</button>
        </div>`;
      box.appendChild(row);
    }
  } catch (e) {
    showToast(false, "Lỗi tải danh sách passkey");
  }
}

/* Modal thêm/sửa passkey */
/* ==== MODAL THÊM / SỬA PASSKEY ==== */
function openAddPassModal() {
  const modal = document.getElementById("pass_edit_full");
  if (!modal)
    return console.error("❌ Không tìm thấy #pass_edit_full trong DOM");

  document.getElementById("pass_edit_title").textContent = "Thêm Passkey";
  document.getElementById("edit_pass_id").value = "";
  document.getElementById("edit_pass_value").value = "";
  document.getElementById("edit_login_pass").value = "";
  document.getElementById("edit_owner").value = "00002";
  document.getElementById("edit_role").value = "user";
  document.getElementById("edit_desc").value = "";
  document.getElementById("edit_active").checked = true;
  document.getElementById("edit_expires").value = "";

  modal.classList.add("show-modal");
}

async function editPasskey(pid) {
  try {
    const r = await fetch("/access/manage_passkey", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "list" }),
    });
    if (!r.ok) throw new Error("Phản hồi không hợp lệ");

    const js = await r.json();
    const p = (js.passwords || []).find((x) => x.id === pid);
    if (!p) {
      showToast(false, "Không tìm thấy passkey");
      return;
    }

    const modal = document.getElementById("pass_edit_full");
    if (!modal)
      return console.error("❌ Không tìm thấy #pass_edit_full trong DOM");

    document.getElementById("pass_edit_title").textContent =
      "Sửa Passkey " + pid;
    document.getElementById("edit_pass_id").value = pid;
    document.getElementById("edit_pass_value").value = "";
    document.getElementById("edit_owner").value = p.owner || "";
    document.getElementById("edit_desc").value = p.description || "";
    document.getElementById("edit_active").checked = !!p.active;
    document.getElementById("edit_expires").value = p.expires_at
      ? new Date(p.expires_at).toISOString().slice(0, 16)
      : "";

    modal.classList.add("show-modal");
    console.log("🟢 Opened edit modal for", pid);
  } catch (e) {
    console.error("Lỗi khi tải passkey:", e);
    showToast(false, "Lỗi khi tải dữ liệu passkey");
  }
}

function closePassEditFull() {
  const modal = document.getElementById("pass_edit_full");
  modal.classList.remove("show-modal");
  modal.style.display = "none";

  // Mở lại danh sách
  document.getElementById("manage_full").classList.add("show-modal");
}

/* ==== LƯU PASSKEY ==== */
async function savePasskey() {
  const pid = document.getElementById("edit_pass_id").value.trim();
  const pass = document.getElementById("edit_pass_value").value.trim();
  const owner = document.getElementById("edit_owner").value.trim();
  const desc = document.getElementById("edit_desc").value.trim();
  const active = document.getElementById("edit_active").checked;
  const expires_at = document.getElementById("edit_expires").value || null;

  const action = pid ? "edit" : "add";

  if (!pid && (!pass || pass.length !== 6 || !/^\d+$/.test(pass))) {
    showToast(false, "Passkey phải gồm 6 chữ số");
    return;
  }

  const payload = {
    action,
    id: pid || null,
    passcode: pass,
    owner,
    description: desc,
    active,
    expires_at,
  };

  console.log("[DEBUG SAVE PAYLOAD]", payload);

  try {
    const r = await fetch("/access/manage_passkey", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const js = await r.json();

    if (js.ok) {
      showToast(true, js.message || "Đã lưu");
      closePassEditFull();
      openManageFull();
      loadPasskeyList();
    } else {
      showToast(false, js.error || "Lưu thất bại");
    }
  } catch (e) {
    console.error(e);
    showToast(false, "Lỗi mạng");
  }
}

/* ==== QUẢN LÝ DANH SÁCH PASSKEY ==== */
function openManageFull() {
  const modal = document.getElementById("manage_full");
  if (!modal) return;
  modal.classList.add("show-modal");
  loadPasskeyList();
}

function closeManageFull() {
  const modal = document.getElementById("manage_full");
  if (modal) modal.classList.remove("show-modal");
}

function openAddPassFull() {
  // Ẩn danh sách
  document.getElementById("manage_full").classList.remove("show-modal");

  // Hiện popup thêm mới
  const modal = document.getElementById("pass_edit_full");
  document.getElementById("pass_edit_title").textContent = "Thêm Passkey";

  // Reset toàn bộ form
  document.getElementById("edit_pass_id").value = "";
  document.getElementById("edit_pass_value").value = "";
  document.getElementById("edit_owner").value = "";
  document.getElementById("edit_desc").value = "";
  document.getElementById("edit_expires").value = "";
  document.getElementById("edit_active").checked = true;

  modal.style.display = "block";
  modal.classList.add("show-modal");
}

/* ==== XOÁ PASSKEY ==== */
let deletePasskeyId = null;

function confirmDeletePasskey(pid) {
  deletePasskeyId = pid;
  const confirmBox = document.getElementById("confirm_backdrop_passkey");

  document.getElementById(
    "confirm_text_passkey"
  ).textContent = `Bạn có chắc muốn xoá passkey "${pid}"?`;
  confirmBox.classList.add("show-modal");
}

function closeConfirmPasskey() {
  document
    .getElementById("confirm_backdrop_passkey")
    .classList.remove("show-modal");
}

async function doDeletePasskey() {
  if (!deletePasskeyId) return;
  try {
    const r = await fetch("/access/manage_passkey", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "delete", id: deletePasskeyId }),
    });
    const js = await r.json();
    if (js.ok) {
      showToast(true, js.message || "Đã xoá passkey");
      closeConfirmPasskey();
      openManageFull(); // 🔹 đảm bảo danh sách bật lại
      loadPasskeyList();
    } else {
      showToast(false, js.error || "Xoá thất bại");
    }
  } catch (e) {
    showToast(false, "Lỗi mạng");
  }
}

/* ==== NOTIFY ==== */
// async function loadFeed() {
//   try {
//     const user_id = getCurrentUserId(); // 🆕 lấy user hiện tại
//     const r = await fetch(`/notify/logs?user_id=${user_id}`); // 🆕 truyền user_id vào query
//     const js = await r.json();
//     const box = document.getElementById("feed");
//     box.innerHTML = "";

//     if (!js.ok || !js.logs.length) {
//       box.innerHTML = '<div class="item i-gray">Không có log nào</div>';
//       return;
//     }

//     for (const it of js.logs) {
//       const div = document.createElement("div");
//       div.className = "item";
//       div.style.borderLeft =
//         it.status === "completed" ? "4px solid #16a34a" : "4px solid #f59e0b";
//       div.textContent = `[${new Date(it.time).toLocaleString()}] ${
//         it.device_id
//       } → ${it.command_type}`;
//       box.appendChild(div);
//     }
//   } catch (e) {
//     showToast(false, "📡 Lỗi tải lịch sử lệnh");
//   }
// }
async function loadFeed() {
  try {
    const user_id = getCurrentUserId();
    const r = await fetch(`/notify/logs?user_id=${user_id}`);
    const js = await r.json();
    const tbody = document.getElementById("feed-body");
    tbody.innerHTML = "";

    if (!js.ok || !js.logs.length) {
      tbody.innerHTML = `<tr><td colspan="4" class="muted">Không có log nào</td></tr>`;
      return;
    }

    for (const it of js.logs) {
      let params = {};
      let result = {};

      try {
        params =
          typeof it.params === "object"
            ? it.params
            : JSON.parse(it.params || "{}");
        result =
          typeof it.result === "object"
            ? it.result
            : JSON.parse(it.result || "{}");
      } catch (err) {
        console.warn("Parse JSON error:", err);
      }

      let resultText = "-";
      let color = "#e2e8f0";

      if (it.device_id.startsWith("fan_")) {
        // quạt → hiển thị trạng thái ON/OFF
        if (params.state) {
          const state = params.state.toLowerCase();
          resultText = state === "on" ? "ON" : "OFF";
          color = state === "on" ? "#16a34a" : "#dc2626";
        }
      } else if (
        it.device_id.startsWith("passkey_") ||
        it.device_id.startsWith("rfid_")
      ) {
        // passkey / rfid → GRANTED / DENIED
        if (result.success === true) {
          resultText = "GRANTED";
          color = "#16a34a";
        } else if (result.success === false) {
          resultText = "DENIED";
          color = "#dc2626";
        }
      }

      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${it.device_id}</td>
        <td style="color:${color};font-weight:600">${resultText}</td>
        <td>${new Date(it.time).toLocaleString("vi-VN")}</td>
      `;
      tbody.appendChild(tr);
    }
  } catch (e) {
    console.error(e);
    showToast(false, "📡 Lỗi tải thông báo");
  }
}

async function loadHistory() {
  const user_id = getCurrentUserId();
  console.log("[DEBUG] loadHistory start, user =", user_id);

  const r = await fetch(`/notify/history?user_id=${user_id}`);
  console.log("[DEBUG] Fetch history response:", r.status);
  const text = await r.text();
  console.log("[DEBUG] Raw response text:", text);

  try {
    const r = await fetch(`/notify/history?user_id=${user_id}`);
    console.log("[DEBUG] Fetch history response:", r.status);
    const js = await r.json();
    console.log("[DEBUG] Lịch sử vào/ra:", js);

    const tbody = document.getElementById("history_table");
    tbody.innerHTML = "";

    if (!js.ok || !js.logs || !js.logs.length) {
      tbody.innerHTML = `<tr><td colspan="5" class="table-empty">Không có lịch sử nào</td></tr>`;
      return;
    }

    for (const log of js.logs) {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${log.device_id}</td>
        <td style="color:${log.result === "granted" ? "#16a34a" : "#dc2626"}">
          ${log.result.toUpperCase()}
        </td>
        <td>${new Date(log.time).toLocaleString("vi-VN")}</td>
      `;
      tbody.appendChild(tr);
    }
  } catch (e) {
    console.error("[DEBUG] loadHistory error:", e);
    showToast(false, "📡 Lỗi tải lịch sử vào/ra");
  }
}

/* ==== RFID ==== */
let RFID_ALL = [];
let RFID_SELECTED = null;
let RFID_IS_ADDING = false; // safe default
let ENROLL_SUPPRESS_DETAIL = false; // đang enroll / thêm -> chặn detail
/*let RECENTLY_ADDED_UID = null;*/ // UID vừa thêm thành công
/*let RECENTLY_ADDED_UNTIL = 0;   */ // timestamp (ms) hết hiệu lực chặn
let ENROLL_STARTED_AT = 0; // timestamp ms: thời điểm bấm "Thêm thẻ"
/* Enroll mode (KHÔNG dùng scan/latest nữa) */
let ENROLL_SESSION = null;
let ENROLL_TIMER = null;
// Ẩn DENIED cho UID này kể từ ENROLL_STARTED_AT đến khi có GRANTED
let HIDE_DENIED_UNTIL_GRANTED_UID = null;

/* Modals */
function openEditModal(title = "Thông tin thẻ") {
  document.getElementById("rfid_modal_title").textContent = title;
  document.getElementById("rfid_modal").classList.add("show-modal");
}
// function closeEditModal() {
//   document.getElementById("rfid_modal").classList.remove("show-modal");
// }
function openLogModal(it) {
  // ❶ đang enroll/add: chặn toàn bộ
  if (ENROLL_SUPPRESS_DETAIL) return;

  document.getElementById("lg_uid").textContent = it.uid || "-";
  document.getElementById("lg_owner").textContent = it.owner || "-";
  document.getElementById("lg_device").textContent = it.device || "-";
  document.getElementById("lg_result").textContent = (
    it.result || "-"
  ).toUpperCase();
  const t = new Date(it.timestamp);
  document.getElementById("lg_time").textContent = t.toLocaleString();
  document.getElementById("log_backdrop").classList.add("show-modal");
}
function closeLogModal() {
  document.getElementById("log_backdrop").classList.remove("show-modal");
}

/* Confirm delete modal */
function openLogModal(it) {
  document.getElementById("lg_uid").textContent = it.rfid_uid || "-";
  document.getElementById("lg_owner").textContent = it.user_id || "-";
  document.getElementById("lg_device").textContent = it.device_id || "-";
  document.getElementById("lg_result").textContent = it.result || "-";
  document.getElementById("lg_time").textContent = new Date(
    it.time
  ).toLocaleString();
  document.getElementById("log_backdrop").classList.add("show-modal");
}
function closeLogModal() {
  document.getElementById("log_backdrop").classList.remove("show-modal");
}

async function loadRfidDevice() {
  const user_id = getCurrentUserId();
  const card = document.querySelector("#tab-rfid .card");

  try {
    const r = await fetch(
      `/access/get_device?user_id=${user_id}&device_type=rfid_gate`
    );
    const js = await r.json();

    if (!js.ok || !js.found) {
      card.classList.add("device-disabled");
      // showRfidMessage("🔒 Bạn không có quyền truy cập thiết bị này", "error");
      showDeviceMessage("rfid_msg", "🔒 Bạn không có quyền truy cập", "error");
      return;
    }

    window.currentRfidDevice = js.device_id;
    window.currentRfidGateway = js.gateway_id;
    card.classList.remove("device-disabled");
    // showRfidMessage(`RFID: ${js.device_id}`, "success");
  } catch (e) {
    console.error(e);
  }
}

// 🔹 Hiển thị thông báo nhỏ

// 🔹 Lấy danh sách log RFID
async function loadRfidCards() {
  try {
    const r = await fetch("/rfid/cards");
    const js = await r.json();
    const tbody = document.getElementById("rfid_card_table");
    tbody.innerHTML = "";

    if (!js.ok || !js.cards?.length) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:#94a3b8">Không có thẻ RFID nào</td></tr>`;
      window.RFID_ALL = []; // <- thêm dòng này
      return;
    }

    // ✅ Lưu toàn bộ mảng vào biến toàn cục
    window.RFID_ALL = js.cards;

    js.cards.forEach((c) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${c.uid}</td>
        <td>${c.user_id}</td>
        <td>${c.card_type || "-"}</td>
        <td>${c.description || "-"}</td>
        <td style="text-align:center;">${c.active ? "✅" : "❌"}</td>
        <td>${new Date(c.registered_at).toLocaleString("vi-VN")}</td>
        <td class="table-actions">
          <button onclick="editRfid('${c.uid}')">✏️</button>
          <button class="delete" onclick="deleteRfid('${c.uid}')">🗑️</button>
        </td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    console.error("Lỗi tải danh sách thẻ:", err);
    document.getElementById("rfid_card_table").innerHTML = `
      <tr><td colspan="7" style="text-align:center;color:red">⚠️ Lỗi tải dữ liệu</td></tr>`;
  }
}

// 🔹 Gọi khi vào tab RFID
document.addEventListener("DOMContentLoaded", () => {
  loadRfidCards();
});

// 🔹 Khi quét RFID xong (gửi request RESTful chuẩn)
async function handleRfidScan(uid) {
  const user_id = getCurrentUserId();
  const dev = window.currentRfidDevice;
  const gateway = window.currentRfidGateway;

  if (!dev || !gateway) {
    showToast(false, "⚠️ Thiếu thông tin thiết bị hoặc gateway");
    return;
  }

  try {
    // ✅ Đúng format RESTful Flask mới: /rfid/<gateway>/<device>
    const resp = await fetch(`/rfid/${gateway}/${dev}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ uid }),
    });

    const js = await resp.json();

    if (!js.ok || js.result !== "granted") {
      showToast(false, "🔒 Thẻ không hợp lệ hoặc không có quyền");
      return;
    }

    showToast(true, "✅ Thẻ hợp lệ, mở cửa thành công!");
    loadRfidLogs();
  } catch (err) {
    console.error(err);
    showToast(false, "📡 RFID lỗi mạng");
  }
}

function generateUID() {
  return Array.from({ length: 8 }, () =>
    Math.floor(Math.random() * 16)
      .toString(16)
      .toUpperCase()
  ).join("");
}

function openAddRfid() {
  // hiển thị modal chờ quét
  document.getElementById("scan_backdrop").classList.add("show-modal");
  document.getElementById("scan_status").innerHTML =
    '<span class="spinner"></span> Đang chờ bạn quét thẻ...';

  // bắt đầu polling backend để chờ UID
  startEnrollPolling();
}
let enrollTimer = null;

async function startEnrollPolling() {
  const start = Date.now();

  enrollTimer = setInterval(async () => {
    try {
      const r = await fetch("/rfid/latest");
      const js = await r.json();

      if (js.ok && js.uid) {
        clearInterval(enrollTimer);
        closeScanModal();

        // mở form thêm + tự điền UID
        openEditModal("Thêm thẻ RFID");
        document.getElementById("f_uid").value = js.uid;
        document.getElementById("rfid_hint").textContent =
          "✅ Thẻ đã được quét thành công!";
      } else if (Date.now() - start > 15000) {
        clearInterval(enrollTimer);
        document.getElementById("scan_status").textContent =
          "⏱️ Hết thời gian chờ, vui lòng thử lại.";
      }
    } catch (e) {
      console.error("Polling error:", e);
    }
  }, 1000); // check mỗi 1 giây
}

function editRfid(uid) {
  RFID_SELECTED = uid; // ✅ thêm dòng này để biết đang sửa thẻ nào

  const card = window.RFID_ALL.find((c) => c.uid === uid);
  if (!card) return;

  document.getElementById("rfid_modal_title").textContent =
    "✏️ Sửa thông tin thẻ";
  document.getElementById("f_uid").value = card.uid;
  document.getElementById("f_owner").value = card.user_id || "";
  document.getElementById("f_type").value = card.card_type || "MIFARE Classic";
  document.getElementById("f_desc").value = card.description || "";
  document.getElementById("f_expires").value = card.expires_at || "";
  document.getElementById("f_active").checked = !!card.active;

  document.getElementById("rfid_modal").classList.add("show-modal");
}

async function saveRfid() {
  const uid = document.getElementById("f_uid").value.trim().toUpperCase();
  const owner = document.getElementById("f_owner").value.trim();

  if (!owner) {
    document.getElementById("rfid_hint").textContent = "⚠️ Chủ thẻ là bắt buộc";
    return;
  }

  const expiresInput = document.getElementById("f_expires").value;
  const expires_at = expiresInput ? new Date(expiresInput).toISOString() : null;

  const data = {
    uid,
    user_id: owner,
    card_type: document.getElementById("f_type").value.trim(),
    description: document.getElementById("f_desc").value.trim(),
    expires_at,
    active: document.getElementById("f_active").checked,
  };

  const url = "/rfid/cards" + (RFID_SELECTED ? `/${RFID_SELECTED}` : "");
  const method = RFID_SELECTED ? "PUT" : "POST";

  try {
    const resp = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    const js = await resp.json();

    if (!js.ok) {
      document.getElementById("rfid_hint").textContent =
        "⚠️ Lỗi: " + (js.error || "Không rõ");
      return;
    }

    closeEditModal();
    showToast(true, RFID_SELECTED ? "Đã cập nhật thẻ" : "Đã thêm thẻ mới");
    loadRfidCards();
    if (!RFID_SELECTED) {
      document.getElementById("f_desc").value = "";
      document.getElementById("f_expires").value = "";
    }
  } catch (err) {
    console.error("Lỗi lưu RFID:", err);
    document.getElementById("rfid_hint").textContent = "⚠️ Lỗi kết nối máy chủ";
  }
}

let DELETE_UID = null;

function deleteRfid(uid) {
  DELETE_UID = uid;
  document.getElementById(
    "confirm_text"
  ).textContent = `Bạn có chắc muốn xoá thẻ UID ${uid}?`;
  document.getElementById("confirm_backdrop").classList.add("show-modal");
}

function closeConfirm() {
  document.getElementById("confirm_backdrop").classList.remove("show-modal");
}

async function confirmDelete() {
  if (!DELETE_UID) return;
  try {
    const r = await fetch(`/rfid/cards/${DELETE_UID}`, { method: "DELETE" });
    const js = await r.json();
    if (!js.ok) throw new Error(js.error || "Lỗi xoá");

    closeConfirm();
    showToast(true, "🗑️ Đã xoá thẻ");
    loadRfidCards();
  } catch (e) {
    showToast(false, "⚠️ Lỗi xoá thẻ");
  }
}
function closeEditModal() {
  RFID_SELECTED = null; // ✅ reset
  document.getElementById("rfid_modal").classList.remove("show-modal");
}

function closeScanModal() {
  document.getElementById("scan_backdrop").classList.remove("show-modal");
  if (enrollTimer) clearInterval(enrollTimer);
}

//////////////////////////
//Passkey login
async function loadPasskeyDevice() {
  const user_id = getCurrentUserId();
  const card = document.querySelector("#tab-passkey .card");
  const msg = document.getElementById("pass_inline");
  const input = document.getElementById("device_id");
  const label = document.getElementById("device_label");

  try {
    const r = await fetch(
      `/access/get_device?user_id=${user_id}&device_type=passkey`
    );
    const js = await r.json();

    if (!js.ok || !js.found) {
      input.value = "";
      input.disabled = true;
      card.classList.add("device-disabled");
      showDeviceMessage(
        "passkey_msg",
        "🔒 Bạn không có quyền truy cập",
        "error"
      );
      msg.style.color = "#d33";
      label.textContent = "Thiết bị: —";
      return;
    }

    // ✅ Lưu biến từ JSON
    const dev = js.device_id;
    const gw = js.gateway_id;

    // ✅ Gán vào hidden input & label
    input.value = dev;
    input.disabled = false;
    input.dataset.gateway_id = gw;

    label.textContent = `Thiết bị: ${dev}`;
    msg.textContent = "";
    msg.style.color = "#555";
    card.classList.remove("device-disabled");

    console.log(`[PASSKEY DEVICE] ${dev} (${gw})`);
  } catch (e) {
    console.error("[loadPasskeyDevice error]", e);
    showDeviceMessage("passkey_msg", "📡 Lỗi tải thiết bị Passkey", "error");
  }
}

/* init */

// DASHBOARD

// window.addEventListener("load", loadTemperatureChart);

// QUYEN SU DUNG

async function loadUserDevices(userId) {
  const r = await fetch(`/devices/for_user/${userId}`);
  const js = await r.json();
  if (!js.ok) {
    alert(js.message || "Không có quyền sử dụng thiết bị nào.");
    disableAllDeviceButtons(); // 🔒 vô hiệu hóa UI
    return;
  }
  renderDevices(js.devices);
}

async function loadFanStatus() {
  const res = await fetch("/fan/status");
  const data = await res.json();

  if (!data.ok) {
    showToast(data.message || "Bạn không thể truy cập thiết bị này", "warning");
    const fanSection = document.getElementById("tab-fan");
    fanSection.classList.add("disabled-device");
    return;
  }

  // Nếu có quyền -> hiển thị thông tin quạt
  console.log("✅ Danh sách quạt:", data.fans);
  // ... (cập nhật label trạng thái, toggle, v.v.)
}
// Lưu user_id vào biến toàn cục khi load trang
window.addEventListener("load", () => {
  const uid = localStorage.getItem("currentUserId");
  if (uid) window.currentUserId = uid;
  document.querySelector('[data-tab="dashboard"]').click();
});
