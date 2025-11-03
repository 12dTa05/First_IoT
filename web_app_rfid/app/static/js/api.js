// ================================
// 🌐 API MODULE (api.js)
// ================================

// Cấu hình API server (tùy local/VPS)
const API_URL = window.API_URL || "http://127.0.0.1:8090";

// Hàm tiện ích chung gọi API
async function apiRequest(path, method = "GET", body = null) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);

  const res = await fetch(`${API_URL}${path}`, opts);
  return await res.json();
}

// ================================
// 🔑 PASSKEY APIs
// ================================
export const PasskeyAPI = {
  list: () => apiRequest("/access/manage_passkey", "POST", { action: "list" }),
  add: (data) =>
    apiRequest("/access/manage_passkey", "POST", { action: "add", ...data }),
  edit: (data) =>
    apiRequest("/access/manage_passkey", "POST", { action: "edit", ...data }),
  delete: (id) =>
    apiRequest("/access/manage_passkey", "POST", { action: "delete", id }),
};

// ================================
// 💳 RFID APIs
// ================================
export const RfidAPI = {
  list: () => apiRequest("/rfid/cards"),
  add: (data) => apiRequest("/rfid/cards", "POST", data),
  edit: (uid, data) => apiRequest(`/rfid/cards/${uid}`, "PUT", data),
  delete: (uid) => apiRequest(`/rfid/cards/${uid}`, "DELETE"),
  latest: () => apiRequest("/rfid/latest"),
};

// ================================
// 🧍 USER & LOGIN APIs
// ================================
export const UserAPI = {
  login: (username, password) =>
    apiRequest("/access/login", "POST", { username, password }),
  checkPermission: (user_id, device_id) =>
    apiRequest(
      `/access/check_permission?user_id=${user_id}&device_id=${device_id}`
    ),
};

// ================================
// 📊 DASHBOARD / HISTORY APIs
// ================================
export const DashboardAPI = {
  history: (user_id) => apiRequest(`/dashboard/history?user_id=${user_id}`),
  temperature: (user_id) =>
    apiRequest(`/dashboard/temperature?user_id=${user_id}`),
};
