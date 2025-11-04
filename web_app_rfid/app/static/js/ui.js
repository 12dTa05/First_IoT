(() => {
  let authToken = null;
  let currentUser = null;
  let devicesCache = [];
  let passkeysCache = [];
  let rfidCardsCache = [];
  let latestReadings = [];
  let currentSensorId = null;
  let temperatureChart = null;
  let toastTimer = null;
  const passkeyContext = { deviceId: null, gatewayId: null };
  let passkeyBuffer = '';
  let passkeySubmitting = false;
  const PASSKEY_CODE_LENGTH = 6;
  const PASSKEY_PLACEHOLDER_CHAR = '·';
  const PASSKEY_FILLED_CHAR = '•';
  let editingRfidUid = null;

  const elements = {};

  const FEATURE_ACCESS = {
    rfid: ['00001'],
    passkey: ['00002'],
    passkeyManagement: ['00002'],
    climate: ['00003'],
    fan: ['00003']
  };


  function isAdminUser() {
    return (currentUser?.role || '').toLowerCase() === 'admin';
  }

  function isFeatureAllowed(feature) {
    if (!feature) return true;
    if (isAdminUser()) return true;
    const allowed = FEATURE_ACCESS[feature];
    if (!allowed) return true;
    const userId = currentUser?.user_id;
    return Boolean(userId && allowed.includes(userId));
  }

  function setFeatureVisibility(element, feature) {
    if (!element) return;
    const allowed = isFeatureAllowed(feature);
    element.classList.toggle('feature-hidden', !allowed);
  }

  function applyFeatureAccessControl() {
    const climateAllowed = isFeatureAllowed('climate');
    setFeatureVisibility(elements.climatePanel, 'climate');
    if (!climateAllowed) {
      currentSensorId = null;
      latestReadings = [];
      destroyTemperatureChart();
    }

    setFeatureVisibility(elements.passkeySection, 'passkey');
    setFeatureVisibility(elements.passkeyTablePanel, 'passkeyManagement');
    setFeatureVisibility(elements.rfidPanel, 'rfid');
  }

  document.addEventListener('DOMContentLoaded', () => {
    cacheElements();
    bindEvents();
    initFromStorage();
    closeRfidModal(true);
    closePasskeyModal(true);
    resetPasskeyState(true);
    setPasskeyInputsDisabled(true);
    elements.passkeySection?.classList.add('no-device');
    setPasskeyStatus('Chưa có thiết bị passkey', 'info');
  });

  function cacheElements() {
    elements.toast = document.getElementById('toast');
    elements.loginModal = document.getElementById('login_modal');
    elements.loginForm = document.getElementById('login_form');
    elements.loginHint = document.getElementById('login_hint');
    elements.loginBtn = document.getElementById('login_btn');
    elements.loginUser = document.getElementById('login_user');
    elements.loginPass = document.getElementById('login_pass');

    elements.appHeader = document.getElementById('app_header');
    elements.appContent = document.getElementById('app_content');
    elements.userName = document.getElementById('user_name');
    elements.logoutBtn = document.getElementById('btn_logout');

    elements.sensorSelect = document.getElementById('sensor_select');
    elements.sensorPeriod = document.getElementById('sensor_period');
    elements.temperatureEmpty = document.getElementById('temperature_empty');
    elements.temperatureSubtitle = document.getElementById('temperature_subtitle');
    elements.latestTemp = document.getElementById('latest_temp');
    elements.latestHumidity = document.getElementById('latest_humidity');
    elements.latestTime = document.getElementById('latest_time');

    elements.accessTable = document.getElementById('access_table_body');
    elements.passkeyTable = document.getElementById('passkey_table_body');
    elements.rfidTable = document.getElementById('rfid_table_body');
    elements.devicesTable = document.getElementById('devices_table_body');

    elements.refreshAccess = document.getElementById('refresh_access');
    elements.refreshPasskey = document.getElementById('refresh_passkey');
    elements.refreshRfid = document.getElementById('refresh_rfid');
    elements.refreshDevices = document.getElementById('refresh_devices');

    elements.addPasskeyBtn = document.getElementById('add_passkey_btn');
    elements.passkeyModal = document.getElementById('passkey_modal');
    elements.passkeyForm = document.getElementById('passkey_form');
    elements.passkeyId = document.getElementById('passkey_id');
    elements.passkeyOwner = document.getElementById('passkey_owner');
    elements.passkeyValue = document.getElementById('passkey_value');
    elements.passkeyDesc = document.getElementById('passkey_desc');
    elements.passkeyExpire = document.getElementById('passkey_expire');
    elements.passkeyActive = document.getElementById('passkey_active');
    elements.passkeyHint = document.getElementById('passkey_hint');
    elements.passkeyCancel = document.getElementById('passkey_cancel');
    elements.passkeySubmit = document.getElementById('passkey_submit');
    elements.passkeyModalTitle = document.getElementById('passkey_modal_title');
    elements.passkeyDisplay = document.getElementById('passkey_display');
    elements.passkeyStatus = document.getElementById('passkey_status');
    elements.passkeyKeypad = document.getElementById('passkey_keypad');
    elements.passkeyEnterBtn = document.getElementById('passkey_enter_btn');
    elements.passkeyClearBtn = document.getElementById('passkey_clear');
    elements.passkeyDeviceLabel = document.getElementById('passkey_device_label');
    elements.passkeySection = document.querySelector('[data-feature="passkey"]');
    elements.passkeyBody = document.querySelector('.passkey-body');
    elements.passkeyTablePanel = document.querySelector('[data-feature="passkey-table"]');
    elements.rfidPanel = document.querySelector('[data-feature="rfid"]');
    elements.climatePanel = document.querySelector('[data-feature="climate"]');
    elements.devicesPanel = document.querySelector('[data-feature="devices"]');

    elements.addRfidBtn = document.getElementById('add_rfid_btn');
    elements.rfidModal = document.getElementById('rfid_modal');
    elements.rfidForm = document.getElementById('rfid_form');
    elements.rfidUid = document.getElementById('rfid_uid');
    elements.rfidOwner = document.getElementById('rfid_owner');
    elements.rfidType = document.getElementById('rfid_type');
    elements.rfidDesc = document.getElementById('rfid_desc');
    elements.rfidExpire = document.getElementById('rfid_expire');
    elements.rfidActive = document.getElementById('rfid_active');
    elements.rfidHint = document.getElementById('rfid_hint');
    elements.rfidCancel = document.getElementById('rfid_cancel');
    elements.rfidSubmit = document.getElementById('rfid_submit');
    elements.rfidModalTitle = document.getElementById('rfid_modal_title');

    elements.statDevicesTotal = document.getElementById('stat_devices_total');
    elements.statDevicesOnline = document.getElementById('stat_devices_online');
    elements.statGatewaysTotal = document.getElementById('stat_gateways_total');
    elements.statGatewaysOnline = document.getElementById('stat_gateways_online');
    elements.statAccessTotal = document.getElementById('stat_access_total');
    elements.statAccessGranted = document.getElementById('stat_access_granted');
    elements.statAlertsTotal = document.getElementById('stat_alerts_total');
    elements.statAlertsDetail = document.getElementById('stat_alerts_detail');
  }

  function bindEvents() {
    elements.loginForm.addEventListener('submit', handleLogin);
    elements.logoutBtn.addEventListener('click', handleLogout);

    elements.sensorSelect.addEventListener('change', () => {
      currentSensorId = elements.sensorSelect.value || null;
      const sensors = getSensorCandidates();
      const selected = sensors.find((s) => s.device_id === currentSensorId);
      if (selected?.reading) {
        updateLatestReadingDisplay(selected.reading);
      } else {
        updateLatestReadingDisplay(null);
      }
      loadTemperatureHistory();
    });

    elements.sensorPeriod.addEventListener('change', () => {
      if (!currentSensorId) return;
      loadTemperatureHistory();
    });

    elements.refreshAccess.addEventListener('click', () => {
      loadAccessLogs(true).catch(() => {});
    });
    if (elements.refreshPasskey) {
      elements.refreshPasskey.addEventListener('click', () => {
        loadPasskeys(true).catch(() => {});
      });
    }
    elements.refreshRfid.addEventListener('click', () => {
      loadRfidCards(true).catch(() => {});
    });
    elements.refreshDevices.addEventListener('click', () => {
      loadDevices(true).catch(() => {});
    });

    if (elements.addRfidBtn) {
      elements.addRfidBtn.addEventListener('click', () => openRfidModal());
    }
    if (elements.rfidCancel) {
      elements.rfidCancel.addEventListener('click', () => closeRfidModal());
    }
    if (elements.rfidForm) {
      elements.rfidForm.addEventListener('submit', (event) => {
        event.preventDefault();
        submitRfidForm().catch((err) => {
          showToast('error', err.message || 'Không thể thêm thẻ');
        });
      });
    }

    if (elements.rfidTable) {
      elements.rfidTable.addEventListener('click', async (event) => {
        const button = event.target.closest('button[data-action]');
        if (!button) return;

        const action = button.dataset.action;
        const uid = button.dataset.uid;
        if (!action || !uid) return;

        if (!isFeatureAllowed('rfid')) {
          showToast('error', 'Bạn không có quyền quản lý thẻ RFID');
          return;
        }

        if (action === 'edit') {
          const card = rfidCardsCache.find((item) => item.uid === uid);
          if (card) {
            openRfidModal(card);
          }
        } else if (action === 'delete') {
          if (!confirm(`Bạn chắc muốn xoá thẻ "${uid}"?`)) return;
          button.disabled = true;
          try {
            await deleteRfidCard(uid);
            showToast('success', 'Đã xoá thẻ RFID');
            await loadRfidCards();
          } catch (err) {
            showToast('error', err.message || 'Không thể xoá thẻ');
          } finally {
            button.disabled = false;
          }
        }
      });
    }

    if (elements.addPasskeyBtn) {
      elements.addPasskeyBtn.addEventListener('click', () => openPasskeyModal());
    }
    if (elements.passkeyCancel) {
      elements.passkeyCancel.addEventListener('click', () => closePasskeyModal());
    }
    if (elements.passkeyForm) {
      elements.passkeyForm.addEventListener('submit', (event) => {
        event.preventDefault();
        submitPasskeyForm().catch((err) => {
          showToast('error', err.message || 'Không thể lưu passkey');
        });
      });
    }

    if (elements.passkeyTable) {
      elements.passkeyTable.addEventListener('click', async (event) => {
        const button = event.target.closest('button[data-action]');
        if (!button) return;
        const action = button.dataset.action;
        const passkeyId = button.dataset.passkeyId;
        if (!action || !passkeyId) return;

        if (!isFeatureAllowed('passkeyManagement')) {
          showToast('error', 'Bạn không có quyền quản lý passkey');
          return;
        }

        if (action === 'edit') {
          const passkey = passkeysCache.find((item) => item.id === passkeyId);
          if (passkey) {
            openPasskeyModal(passkey);
          }
        } else if (action === 'delete') {
          if (!confirm(`Bạn chắc muốn xoá passkey "${passkeyId}"?`)) return;
          button.disabled = true;
          try {
            await modifyPasskey('delete', { id: passkeyId });
            showToast('success', 'Đã xoá passkey');
            await loadPasskeys();
          } catch (err) {
            showToast('error', err.message || 'Không thể xoá passkey');
          } finally {
            button.disabled = false;
          }
        }
      });
    }

    if (elements.passkeyKeypad) {
      elements.passkeyKeypad.addEventListener('click', handlePasskeyKeypadClick);
    }
    if (elements.passkeyClearBtn) {
      elements.passkeyClearBtn.addEventListener('click', () => {
        resetPasskeyState(true);
        if (passkeyContext.deviceId) {
          setPasskeyStatus('Nhập passkey để mở khoá', 'info');
        }
      });
    }
    if (elements.passkeyEnterBtn) {
      elements.passkeyEnterBtn.addEventListener('click', () => {
        submitPasskeyCode();
      });
    }

    document.addEventListener('keydown', handlePasskeyKeydown);

    elements.devicesTable.addEventListener('click', async (event) => {
      const btn = event.target.closest('button[data-action]');
      if (!btn) return;

      const action = btn.dataset.action;
      const deviceId = btn.dataset.deviceId;
      const gatewayId = btn.dataset.gatewayId;

      if (!action || !deviceId || !gatewayId) return;

      btn.disabled = true;
      try {
        await sendCommand(gatewayId, deviceId, action);
        showToast('success', 'Đã gửi lệnh tới thiết bị');
      } catch (err) {
        showToast('error', err.message || 'Không thể gửi lệnh');
      } finally {
        btn.disabled = false;
      }
    });
  }

  async function initFromStorage() {
    const savedToken = localStorage.getItem('iot_auth_token');
    if (savedToken) {
      authToken = savedToken;
      try {
        const me = await apiFetch('/api/auth/me');
        currentUser = me;
        onLoginSuccess(false);
        return;
      } catch (err) {
        localStorage.removeItem('iot_auth_token');
        authToken = null;
        showToast('error', 'Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại');
      }
    }

    toggleLoginModal(true);
    elements.loginUser.focus();
  }

  function toggleLoginModal(show) {
    elements.loginModal.classList.toggle('show', show);
    if (show) {
      elements.loginHint.textContent = '';
      elements.loginForm.reset();
      elements.loginUser.focus();
    }
  }

  async function handleLogin(event) {
    event.preventDefault();
    const username = elements.loginUser.value.trim();
    const password = elements.loginPass.value.trim();

    if (!username || !password) {
      elements.loginHint.textContent = 'Vui lòng nhập đầy đủ tài khoản và mật khẩu';
      return;
    }

    setLoginLoading(true);
    try {
      const payload = await apiFetch('/api/auth/login', {
        method: 'POST',
        body: { username, password },
        auth: false
      });

      if (!payload?.token || !payload?.user) {
        throw new Error('Phản hồi đăng nhập không hợp lệ');
      }

      authToken = payload.token;
      currentUser = payload.user;
      localStorage.setItem('iot_auth_token', authToken);
      onLoginSuccess(true);
    } catch (err) {
      elements.loginHint.textContent = err.message || 'Đăng nhập thất bại';
      showToast('error', elements.loginHint.textContent);
    } finally {
      setLoginLoading(false);
    }
  }

  function setLoginLoading(loading) {
    elements.loginBtn.disabled = loading;
    elements.loginBtn.textContent = loading ? 'Đang đăng nhập...' : 'Đăng nhập';
  }

  function onLoginSuccess(showGreeting) {
    toggleLoginModal(false);
    elements.appHeader.classList.remove('hidden');
    elements.appContent.classList.remove('hidden');

    const displayName = currentUser.full_name || currentUser.username || currentUser.user_id;
    elements.userName.textContent = displayName || 'Người dùng';

    updatePasskeyControls();
    updateRfidControls();

    if (showGreeting) {
      showToast('success', `Xin chào ${displayName}`);
    }

    loadAllData().catch((err) => {
      showToast('error', err.message || 'Không thể tải dữ liệu ban đầu');
    });
  }

  async function loadAllData() {
    setTableMessage(elements.accessTable, 5, 'Đang tải dữ liệu...');
    if (elements.passkeyTable) {
      setTableMessage(elements.passkeyTable, 6, 'Đang tải dữ liệu...');
    }
    setTableMessage(elements.rfidTable, 5, 'Đang tải dữ liệu...');
    setTableMessage(elements.devicesTable, 6, 'Đang tải dữ liệu...');

    await Promise.all([
      loadOverview(),
      loadPasskeys(false),
      loadDevices(false),
      loadAccessLogs(false),
      loadRfidCards(false)
    ]);
  }

  async function loadOverview() {
    try {
      const res = await apiFetch('/api/dashboard/overview');
      const data = res?.data || res || {};

      updateStats(data);
      updateTemperatureSensors(data.latest_readings || []);
    } catch (err) {
      updateStats();
      updateTemperatureSensors([]);
      throw err;
    }
  }

  function updateStats(data = {}) {
    const devices = data.devices || {};
    const gateways = data.gateways || {};
    const access = data.access || {};
    const alerts = data.alerts || {};

    elements.statDevicesTotal.textContent = formatNumber(devices.total_devices);
    elements.statDevicesOnline.textContent = formatNumber(devices.online_devices);
    elements.statGatewaysTotal.textContent = formatNumber(gateways.total_gateways);
    elements.statGatewaysOnline.textContent = formatNumber(gateways.online_gateways);
    elements.statAccessTotal.textContent = formatNumber(access.total_access);
    elements.statAccessGranted.textContent = formatNumber(access.granted);
    elements.statAlertsTotal.textContent = formatNumber(alerts.alert_count);
    elements.statAlertsDetail.textContent =
      alerts.alert_count > 0 ? 'Cần kiểm tra hệ thống' : 'Không có cảnh báo mới';
  }

  function updateTemperatureSensors(readings) {
    latestReadings = Array.isArray(readings) ? readings : [];
    refreshSensorOptions();
  }

  function getSensorCandidates() {
    if (latestReadings.length) {
      return latestReadings
        .filter((reading) => reading && reading.device_id)
        .map((reading) => ({
          device_id: reading.device_id,
          label: reading.device_id,
          reading
        }));
    }

    return devicesCache
      .filter((device) => {
        const type = String(device.device_type || '').toLowerCase();
        return (
          type.includes('temp') ||
          type.includes('climate') ||
          type.includes('environment') ||
          type.includes('sensor')
        );
      })
      .map((device) => ({
        device_id: device.device_id,
        label: device.device_id || device.device_type || 'Sensor',
        reading: null
      }));
  }

  function refreshSensorOptions() {
    if (!elements.sensorSelect) {
      return;
    }

    const sensors = getSensorCandidates();
    elements.sensorSelect.innerHTML = '';

    if (!sensors.length) {
      elements.sensorSelect.disabled = true;
      currentSensorId = null;
      updateLatestReadingDisplay(null);
      destroyTemperatureChart();
      elements.temperatureEmpty.classList.remove('hidden');
      return;
    }

    elements.sensorSelect.disabled = false;
    elements.temperatureEmpty.classList.add('hidden');

    sensors.forEach((sensor) => {
      const option = document.createElement('option');
      option.value = sensor.device_id;
      option.textContent = sensor.label;
      elements.sensorSelect.appendChild(option);
    });

    if (!currentSensorId || !sensors.some((sensor) => sensor.device_id === currentSensorId)) {
      currentSensorId = sensors[0].device_id;
    }

    elements.sensorSelect.value = currentSensorId;

    const selectedSensor = sensors.find((sensor) => sensor.device_id === currentSensorId);
    if (selectedSensor?.reading) {
      updateLatestReadingDisplay(selectedSensor.reading);
    }

    loadTemperatureHistory();
  }

  function refreshPasskeyContext() {
    if (!isFeatureAllowed('passkey')) {
      elements.passkeySection?.classList.add('no-device');
      setPasskeyInputsDisabled(true);
      passkeyContext.deviceId = null;
      passkeyContext.gatewayId = null;
      return;
    }

    const candidate = devicesCache.find((device) => {
      const type = String(device.device_type || '').toLowerCase();
      return type.includes('passkey') || type.includes('keypad') || type.includes('door');
    });

    const previousDeviceId = passkeyContext.deviceId;
    const availableBefore = Boolean(previousDeviceId);
    if (candidate) {
      passkeyContext.deviceId = candidate.device_id;
      passkeyContext.gatewayId = candidate.gateway_id;
    } else {
      passkeyContext.deviceId = null;
      passkeyContext.gatewayId = null;
    }

    if (elements.passkeyDeviceLabel) {
      elements.passkeyDeviceLabel.textContent = candidate
        ? `Thiết bị: ${candidate.device_id}`
        : 'Thiết bị: —';
    }

    if (!candidate) {
      elements.passkeySection?.classList.add('no-device');
      setPasskeyInputsDisabled(true);
      resetPasskeyState(true);
      setPasskeyStatus('Không tìm thấy thiết bị passkey phù hợp', 'error');
      return;
    }

    elements.passkeySection?.classList.remove('no-device');

    if (!passkeySubmitting) {
      setPasskeyInputsDisabled(false);
    }
    if (!availableBefore || previousDeviceId !== passkeyContext.deviceId || !passkeyBuffer.length) {
      resetPasskeyState(true);
      setPasskeyStatus('Nhập passkey để mở khoá', 'info');
    }
  }

  function setPasskeyInputsDisabled(disabled) {
    if (elements.passkeyKeypad) {
      elements.passkeyKeypad.querySelectorAll('button').forEach((btn) => {
        btn.disabled = disabled;
      });
    }
    if (elements.passkeyEnterBtn) {
      elements.passkeyEnterBtn.disabled = disabled;
    }
    if (elements.passkeyClearBtn) {
      elements.passkeyClearBtn.disabled = disabled;
    }
  }

  function setPasskeyStatus(message, type = 'info') {
    if (!elements.passkeyStatus) return;
    const statusEl = elements.passkeyStatus;
    statusEl.textContent = message || '';
    statusEl.classList.remove('success', 'error', 'muted');
    if (!message) {
      statusEl.classList.add('muted');
      return;
    }
    if (type === 'success') {
      statusEl.classList.add('success');
    } else if (type === 'error') {
      statusEl.classList.add('error');
    } else {
      statusEl.classList.add('muted');
    }
  }

  function updatePasskeyDisplay() {
    if (!elements.passkeyDisplay) return;
    const filled = PASSKEY_FILLED_CHAR.repeat(Math.min(passkeyBuffer.length, PASSKEY_CODE_LENGTH));
    const remaining = Math.max(PASSKEY_CODE_LENGTH - passkeyBuffer.length, 0);
    const placeholders = PASSKEY_PLACEHOLDER_CHAR.repeat(remaining);
    const value = (filled + placeholders) || PASSKEY_PLACEHOLDER_CHAR.repeat(PASSKEY_CODE_LENGTH);
    elements.passkeyDisplay.textContent = value;
  }

  function resetPasskeyState(clearStatus = false) {
    passkeyBuffer = '';
    updatePasskeyDisplay();
    if (clearStatus) {
      setPasskeyStatus('', 'info');
    }
  }

  function appendPasskeyDigit(digit) {
    if (!isFeatureAllowed('passkey') || passkeySubmitting || !passkeyContext.deviceId) return;
    if (passkeyBuffer.length >= PASSKEY_CODE_LENGTH) return;
    passkeyBuffer += digit;
    updatePasskeyDisplay();
    if (passkeyBuffer.length === PASSKEY_CODE_LENGTH) {
      setPasskeyStatus('Nhấn "Gửi lệnh" để mở khoá', 'info');
    } else {
      setPasskeyStatus('', 'info');
    }
  }

  function removePasskeyDigit() {
    if (!isFeatureAllowed('passkey') || passkeySubmitting) return;
    if (!passkeyBuffer.length) return;
    passkeyBuffer = passkeyBuffer.slice(0, -1);
    updatePasskeyDisplay();
    if (!passkeyBuffer.length && passkeyContext.deviceId) {
      setPasskeyStatus('Nhập passkey để mở khoá', 'info');
    }
  }

  function handlePasskeyKeypadClick(event) {
    const button = event.target.closest('button');
    if (!button || button.disabled) return;
    if (!isFeatureAllowed('passkey')) return;

    if (button.dataset.digit !== undefined) {
      appendPasskeyDigit(button.dataset.digit);
      return;
    }

    const action = button.dataset.action;
    if (action === 'backspace') {
      removePasskeyDigit();
    } else if (action === 'clear') {
      resetPasskeyState(true);
      if (passkeyContext.deviceId) {
        setPasskeyStatus('Nhập passkey để mở khoá', 'info');
      }
    }
  }

  function handlePasskeyKeydown(event) {
    if (!passkeyContext.deviceId || passkeySubmitting) return;
    if (!elements.appContent || elements.appContent.classList.contains('hidden')) return;
    const targetTag = (event.target && event.target.tagName) || '';
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(targetTag)) return;

    if (/^\d$/.test(event.key)) {
      event.preventDefault();
      appendPasskeyDigit(event.key);
    } else if (event.key === 'Backspace') {
      event.preventDefault();
      removePasskeyDigit();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      submitPasskeyCode();
    } else if (event.key === 'Escape') {
      resetPasskeyState(true);
      if (passkeyContext.deviceId) {
        setPasskeyStatus('Nhập passkey để mở khoá', 'info');
      }
    }
  }

  async function submitPasskeyCode() {
    if (passkeySubmitting) return;

    if (!passkeyContext.deviceId || !passkeyContext.gatewayId) {
      setPasskeyStatus('Không tìm thấy thiết bị passkey', 'error');
      return;
    }

    if (passkeyBuffer.length !== PASSKEY_CODE_LENGTH) {
      setPasskeyStatus(`Passkey phải gồm ${PASSKEY_CODE_LENGTH} số.`, 'error');
      return;
    }

    if (!currentUser?.user_id) {
      setPasskeyStatus('Thiếu thông tin tài khoản hiện tại.', 'error');
      return;
    }

    passkeySubmitting = true;
    setPasskeyInputsDisabled(true);
    setPasskeyStatus('Đang gửi lệnh...', 'info');

    try {
      const response = await fetch(
        `/access/${encodeURIComponent(passkeyContext.gatewayId)}/${encodeURIComponent(
          passkeyContext.deviceId
        )}/passcode`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            passcode: passkeyBuffer,
            user_id: currentUser.user_id
          })
        }
      );

      const data = await response.json().catch(() => ({}));

      if (!response.ok || data.ok === false) {
        const message = data.deny_reason || data.error || data.detail || 'Không thể gửi passkey';
        setPasskeyStatus(message, 'error');
        showToast('error', message);
        return;
      }

      if (data.result === 'granted') {
        resetPasskeyState(false);
        setPasskeyStatus('Mở khoá thành công', 'success');
        showToast('success', 'Đã mở khoá thiết bị');
        loadAccessLogs(true).catch(() => {});
      } else {
        const message = data.deny_reason || 'Passkey không hợp lệ';
        setPasskeyStatus(message, 'error');
        showToast('error', message);
      }
    } catch (err) {
      setPasskeyStatus(err.message || 'Không thể gửi passkey', 'error');
      showToast('error', err.message || 'Không thể gửi passkey');
    } finally {
      passkeySubmitting = false;
      setPasskeyInputsDisabled(!passkeyContext.deviceId);
    }
  }

  function updatePasskeyControls() {
    const controlAllowed = isFeatureAllowed('passkey');
    const managementAllowed = isFeatureAllowed('passkeyManagement');

    setFeatureVisibility(elements.passkeySection, 'passkey');
    setFeatureVisibility(elements.passkeyTablePanel, 'passkeyManagement');

    if (!controlAllowed) {
      closePasskeyModal(true);
      resetPasskeyState(true);
      setPasskeyInputsDisabled(true);
      elements.passkeySection?.classList.add('no-device');
      setPasskeyStatus('Bạn không có quyền sử dụng passkey', 'error');
    } else {
      refreshPasskeyContext();
    }

    if (elements.addPasskeyBtn) {
      elements.addPasskeyBtn.classList.toggle('hidden', !managementAllowed);
    }

    if (elements.passkeyOwner && currentUser?.user_id && managementAllowed) {
      elements.passkeyOwner.value = currentUser.user_id;
    }

    if (!managementAllowed) {
      closePasskeyModal(true);
    }
  }

  async function loadPasskeys(isRefresh) {
    if (!elements.passkeyTable) return;

    if (isRefresh) {
      setTableMessage(elements.passkeyTable, 6, 'Đang tải dữ liệu...');
    }

    try {
      const response = await fetch('/access/manage_passkey', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'list' })
      });

      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || data.detail || 'Không thể tải passkey');
      }

      passkeysCache = Array.isArray(data.passwords) ? data.passwords : [];
      const isAdmin = (currentUser?.role || '').toLowerCase() === 'admin';
      const currentId = currentUser?.user_id;
      const visiblePasskeys = isAdmin
        ? passkeysCache
        : passkeysCache.filter((item) => item.owner === currentId);

      if (!visiblePasskeys.length) {
        setTableMessage(
          elements.passkeyTable,
          6,
          isAdmin ? 'Chưa có passkey nào' : 'Bạn chưa có passkey nào'
        );
        return;
      }

      elements.passkeyTable.innerHTML = visiblePasskeys
        .map((item) => {
          const active = Boolean(item.active);
          const statusClass = active ? 'status-online' : 'status-offline';
          const statusLabel = active ? 'HOẠT ĐỘNG' : 'NGỪNG';
          const canManage = isAdmin || item.owner === currentId;
          const actions = canManage
            ? `<div class="action-buttons"><button data-action="edit" data-passkey-id="${escapeHtml(item.id)}">Sửa</button><button data-action="delete" data-passkey-id="${escapeHtml(item.id)}">Xoá</button></div>`
            : '<span class="muted">Không có hành động</span>';

          return `
            <tr>
              <td>${escapeHtml(item.id)}</td>
              <td>${escapeHtml(item.owner)}</td>
              <td><span class="status-pill ${statusClass}">${statusLabel}</span></td>
              <td>${formatDateTime(item.expires_at)}</td>
              <td>${escapeHtml(item.description || '')}</td>
              <td>${actions}</td>
            </tr>
          `;
        })
        .join('');
    } catch (err) {
      setTableMessage(
        elements.passkeyTable,
        6,
        err.message || 'Không thể tải passkey'
      );
      throw err;
    }
  }

  async function modifyPasskey(action, payload) {
    const response = await fetch('/access/manage_passkey', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ...payload })
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      throw new Error(data.error || data.detail || 'Yêu cầu thất bại');
    }

    return data;
  }

  function openPasskeyModal(passkey) {
    if (!isFeatureAllowed('passkeyManagement') || !elements.passkeyModal) return;

    if (elements.passkeyForm) {
      elements.passkeyForm.reset();
    }
    if (elements.passkeyHint) {
      elements.passkeyHint.textContent = '';
    }

    const editing = Boolean(passkey);
    if (elements.passkeyModalTitle) {
      elements.passkeyModalTitle.textContent = editing ? 'Sửa passkey' : 'Thêm passkey';
    }

    if (elements.passkeyId) {
      elements.passkeyId.value = editing ? passkey.id : '';
    }

    const isAdmin = isAdminUser();

    if (elements.passkeyOwner) {
      elements.passkeyOwner.value =
        passkey?.owner || currentUser?.user_id || '';
      elements.passkeyOwner.readOnly = !isAdmin;
    }

    if (elements.passkeyValue) {
      elements.passkeyValue.value = '';
      elements.passkeyValue.disabled = editing;
      elements.passkeyValue.placeholder = editing ? 'Không thể sửa passcode' : 'VD: 123456';
    }

    if (elements.passkeyDesc) {
      elements.passkeyDesc.value = passkey?.description || '';
    }

    if (elements.passkeyExpire) {
      if (passkey?.expires_at) {
        try {
          const date = new Date(passkey.expires_at);
          if (!Number.isNaN(date.getTime())) {
            elements.passkeyExpire.value = date.toISOString().slice(0, 16);
          } else {
            elements.passkeyExpire.value = '';
          }
        } catch {
          elements.passkeyExpire.value = '';
        }
      } else {
        elements.passkeyExpire.value = '';
      }
    }

    if (elements.passkeyActive) {
      elements.passkeyActive.checked = passkey ? Boolean(passkey.active) : true;
    }

    elements.passkeyModal.classList.add('show');
    if (!editing && elements.passkeyValue) {
      elements.passkeyValue.focus();
    } else if (editing && elements.passkeyDesc) {
      elements.passkeyDesc.focus();
    }
  }

  function closePasskeyModal(force) {
    if (!elements.passkeyModal) return;
    elements.passkeyModal.classList.remove('show');
    if (!force && elements.passkeyForm) {
      elements.passkeyForm.reset();
    }
    if (elements.passkeyHint) {
      elements.passkeyHint.textContent = '';
    }
    if (elements.passkeyValue) {
      elements.passkeyValue.disabled = false;
      elements.passkeyValue.placeholder = 'VD: 123456';
    }
    if (elements.passkeyOwner) {
      elements.passkeyOwner.readOnly = false;
    }
  }

  async function submitPasskeyForm() {
    if (!elements.passkeyForm) return;

    if (!isFeatureAllowed('passkeyManagement')) {
      throw new Error('Bạn không có quyền quản lý passkey');
    }

    const role = (currentUser?.role || '').toLowerCase();
    const isAdmin = role === 'admin';

    const id = (elements.passkeyId?.value || '').trim();
    let owner = (elements.passkeyOwner?.value || '').trim();
    const passcode = (elements.passkeyValue?.value || '').trim();
    const description = (elements.passkeyDesc?.value || '').trim();
    const expiresRaw = elements.passkeyExpire?.value || '';
    const active = Boolean(elements.passkeyActive?.checked);

    if (!isAdmin) {
      owner = currentUser?.user_id || owner;
    }

    if (!owner) {
      if (elements.passkeyHint) elements.passkeyHint.textContent = 'Vui lòng nhập user_id.';
      throw new Error('Thiếu user_id');
    }

    let expiresAt = null;
    if (expiresRaw) {
      const parsed = new Date(expiresRaw);
      if (Number.isNaN(parsed.getTime())) {
        if (elements.passkeyHint) elements.passkeyHint.textContent = 'Thời gian hết hạn không hợp lệ.';
        throw new Error('Thời gian hết hạn không hợp lệ');
      }
      expiresAt = parsed.toISOString();
    }

    const submitBtn = elements.passkeySubmit;
    if (!isAdmin && id) {
      const target = passkeysCache.find((item) => item.id === id);
      if (target && target.owner !== owner) {
        throw new Error('Bạn không có quyền chỉnh sửa passkey này');
      }
    }
    const previousText = submitBtn ? submitBtn.textContent : '';
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Đang lưu...';
    }

    try {
      if (id) {
        await modifyPasskey('edit', {
          id,
          owner,
          description: description || '',
          active,
          expires_at: expiresAt
        });
      } else {
        if (!/^\d{6}$/.test(passcode)) {
          if (elements.passkeyHint) {
            elements.passkeyHint.textContent = 'Passcode phải gồm đúng 6 số.';
          }
          throw new Error('Passcode không hợp lệ');
        }

        await modifyPasskey('add', {
          owner,
          passcode,
          description: description || '',
          active,
          expires_at: expiresAt
        });
      }

      showToast('success', id ? 'Đã cập nhật passkey' : 'Đã thêm passkey');
      closePasskeyModal();
      await loadPasskeys();
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = previousText || 'Lưu';
      }
    }
  }

  function updateRfidControls() {
    const allowed = isFeatureAllowed('rfid');
    setFeatureVisibility(elements.rfidPanel, 'rfid');

    if (elements.addRfidBtn) {
      elements.addRfidBtn.classList.toggle('hidden', !allowed);
    }

    if (!allowed) {
      closeRfidModal(true);
      editingRfidUid = null;
      rfidCardsCache = [];
      if (elements.rfidTable) {
        setTableMessage(elements.rfidTable, 5, 'Bạn không có quyền xem thẻ RFID');
      }
      return;
    }

    if (elements.rfidOwner && currentUser?.user_id) {
      elements.rfidOwner.value = currentUser.user_id;
    }
  }

  function openRfidModal(card) {
    if (!isFeatureAllowed('rfid') || !elements.rfidModal) return;

    if (elements.rfidForm) {
      elements.rfidForm.reset();
    }

    const editing = Boolean(card);
    editingRfidUid = editing ? card.uid : null;

    if (elements.rfidModalTitle) {
      elements.rfidModalTitle.textContent = editing ? 'Sửa thẻ RFID' : 'Thêm thẻ RFID';
    }

    if (elements.rfidHint) elements.rfidHint.textContent = '';

    if (elements.rfidUid) {
      elements.rfidUid.value = editing ? card.uid : '';
      elements.rfidUid.readOnly = editing;
      elements.rfidUid.placeholder = editing ? 'Không thể sửa UID' : 'VD: A1B2C3D4';
    }

    if (elements.rfidOwner) {
      elements.rfidOwner.value = editing
        ? card.user_id || ''
        : currentUser?.user_id || '';
    }

    if (elements.rfidType) {
      elements.rfidType.value = editing
        ? card.card_type || 'MIFARE Classic'
        : 'MIFARE Classic';
    }

    if (elements.rfidDesc) {
      elements.rfidDesc.value = editing ? card.description || '' : '';
    }

    if (elements.rfidExpire) {
      if (editing && card.expires_at) {
        try {
          const date = new Date(card.expires_at);
          elements.rfidExpire.value = Number.isNaN(date.getTime())
            ? ''
            : date.toISOString().slice(0, 16);
        } catch {
          elements.rfidExpire.value = '';
        }
      } else {
        elements.rfidExpire.value = '';
      }
    }

    if (elements.rfidActive) {
      elements.rfidActive.checked = editing ? Boolean(card.active) : true;
    }

    elements.rfidModal.classList.add('show');
    if (!editing && elements.rfidUid) {
      elements.rfidUid.focus();
    } else if (editing && elements.rfidDesc) {
      elements.rfidDesc.focus();
    }
  }

  function closeRfidModal(force) {
    if (!elements.rfidModal) return;
    elements.rfidModal.classList.remove('show');
    if (!force && elements.rfidForm) {
      elements.rfidForm.reset();
    }
    if (elements.rfidHint) {
      elements.rfidHint.textContent = '';
    }
    if (elements.rfidUid) {
      elements.rfidUid.readOnly = false;
      elements.rfidUid.placeholder = 'VD: A1B2C3D4';
    }
    editingRfidUid = null;
  }

  async function submitRfidForm() {
    if (!elements.rfidForm) return;

    if (!isFeatureAllowed('rfid')) {
      throw new Error('Bạn không có quyền thêm thẻ RFID');
    }

    const uid = (elements.rfidUid?.value || '').trim().toUpperCase();
    const userId = (elements.rfidOwner?.value || '').trim();
    const cardType = (elements.rfidType?.value || 'MIFARE Classic').trim();
    const description = (elements.rfidDesc?.value || '').trim();
    const expiresRaw = elements.rfidExpire?.value || '';
    const active = Boolean(elements.rfidActive?.checked);

    if (!uid && !editingRfidUid) {
      if (elements.rfidHint) elements.rfidHint.textContent = 'Vui lòng nhập UID thẻ.';
      throw new Error('Thiếu UID thẻ');
    }

    if (!userId) {
      if (elements.rfidHint) elements.rfidHint.textContent = 'Vui lòng nhập user_id của chủ thẻ.';
      throw new Error('Thiếu user_id');
    }

    let expiresAt = null;
    if (expiresRaw) {
      const parsed = new Date(expiresRaw);
      if (Number.isNaN(parsed.getTime())) {
        if (elements.rfidHint) elements.rfidHint.textContent = 'Thời gian hết hạn không hợp lệ.';
        throw new Error('Thời gian hết hạn không hợp lệ');
      }
      expiresAt = parsed.toISOString();
    }

    const payload = {
      user_id: userId,
      card_type: cardType || 'MIFARE Classic',
      description: description || null,
      expires_at: expiresAt,
      active
    };

    const submitBtn = elements.rfidSubmit;
    const previousText = submitBtn ? submitBtn.textContent : '';
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = 'Đang lưu...';
    }

    try {
      if (editingRfidUid) {
        const response = await fetch(`/rfid/cards/${encodeURIComponent(editingRfidUid)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        const data = await response.json().catch(() => ({}));

        if (!response.ok || !data.ok) {
          const message = data.error || data.detail || 'Không thể cập nhật thẻ';
          if (elements.rfidHint) elements.rfidHint.textContent = message;
          throw new Error(message);
        }
        showToast('success', data.msg || 'Đã cập nhật thẻ RFID');
      } else {
        const response = await fetch('/rfid/cards', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ uid, ...payload })
        });

        const data = await response.json().catch(() => ({}));

        if (!response.ok || !data.ok) {
          const message = data.error || data.detail || 'Không thể thêm thẻ';
          if (elements.rfidHint) elements.rfidHint.textContent = message;
          throw new Error(message);
        }
        showToast('success', data.msg || 'Đã thêm thẻ RFID');
      }

      closeRfidModal();
      await loadRfidCards();
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = previousText || 'Thêm thẻ';
      }
    }
  }

  async function deleteRfidCard(uid) {
    const response = await fetch(`/rfid/cards/${encodeURIComponent(uid)}`, {
      method: 'DELETE'
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      throw new Error(data.error || data.detail || 'Không thể xoá thẻ');
    }
    return data;
  }

  function updateLatestReadingDisplay(reading) {
    if (!reading) {
      elements.temperatureSubtitle.textContent = 'Chưa có dữ liệu cảm biến';
      elements.latestTemp.textContent = '--';
      elements.latestHumidity.textContent = '--';
      elements.latestTime.textContent = '--';
      return;
    }

    elements.temperatureSubtitle.textContent = `Cảm biến: ${reading.device_id || ''}`;
    elements.latestTemp.textContent =
      reading.temperature !== undefined && reading.temperature !== null
        ? `${Number(reading.temperature).toFixed(1)}°C`
        : '--';
    elements.latestHumidity.textContent =
      reading.humidity !== undefined && reading.humidity !== null
        ? `${Number(reading.humidity).toFixed(1)}%`
        : '--';
    elements.latestTime.textContent = formatDateTime(reading.time);
  }

  async function loadTemperatureHistory() {
    if (!currentSensorId) {
      destroyTemperatureChart();
      elements.temperatureEmpty.classList.remove('hidden');
      return;
    }

    const hours = parseInt(elements.sensorPeriod.value, 10) || 24;
    const params = new URLSearchParams({
      device_id: currentSensorId,
      hours: String(hours)
    });

    try {
      const res = await apiFetch(`/api/dashboard/temperature-history?${params.toString()}`);
      const history = res?.data || res || [];

      if (history.length) {
        const latestPoint = history[history.length - 1];
        updateLatestReadingDisplay({
          device_id: currentSensorId,
          temperature: latestPoint.temperature,
          humidity: latestPoint.humidity,
          time: latestPoint.time
        });
      } else if (!latestReadings.some((reading) => reading.device_id === currentSensorId)) {
        updateLatestReadingDisplay(null);
      }

      renderTemperatureChart(history);
    } catch (err) {
      showToast('error', err.message || 'Không thể tải biểu đồ nhiệt độ');
    }
  }

  function renderTemperatureChart(data) {
    const rows = Array.isArray(data) ? data : [];

    if (!rows.length) {
      destroyTemperatureChart();
      elements.temperatureEmpty.classList.remove('hidden');
      return;
    }

    elements.temperatureEmpty.classList.add('hidden');

    const labels = rows.map((row) => formatTime(row.time));
    const temperatures = rows.map((row) => Number(row.temperature));
    const humidities = rows.map((row) => Number(row.humidity));

    const ctx = document.getElementById('temperature_chart').getContext('2d');

    if (temperatureChart) {
      temperatureChart.data.labels = labels;
      temperatureChart.data.datasets[0].data = temperatures;
      temperatureChart.data.datasets[1].data = humidities;
      temperatureChart.update();
      return;
    }

    temperatureChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'Nhiệt độ (°C)',
            data: temperatures,
            borderColor: 'rgba(239, 68, 68, 0.9)',
            backgroundColor: 'rgba(254, 202, 202, 0.35)',
            tension: 0.35,
            fill: true
          },
          {
            label: 'Độ ẩm (%)',
            data: humidities,
            borderColor: 'rgba(37, 99, 235, 0.9)',
            backgroundColor: 'rgba(191, 219, 254, 0.35)',
            tension: 0.35,
            fill: true
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'bottom'
          }
        },
        scales: {
          y: {
            beginAtZero: false
          }
        }
      }
    });
  }

  function destroyTemperatureChart() {
    if (temperatureChart) {
      temperatureChart.destroy();
      temperatureChart = null;
    }
  }

  async function loadDevices(isRefresh) {
    if (isRefresh) {
      setTableMessage(elements.devicesTable, 6, 'Đang tải dữ liệu...');
    }

    try {
      const res = await apiFetch('/api/devices/');
      const list = Array.isArray(res) ? res : res?.data || [];
      devicesCache = list;
      renderDevices(list);
      if (isFeatureAllowed('passkey')) {
        refreshPasskeyContext();
      } else {
        passkeyContext.deviceId = null;
        passkeyContext.gatewayId = null;
      }

      if (!latestReadings.length && isFeatureAllowed('climate')) {
        refreshSensorOptions();
      }
    } catch (err) {
      setTableMessage(elements.devicesTable, 6, 'Không thể tải danh sách thiết bị');
      if (isFeatureAllowed('passkey')) {
        refreshPasskeyContext();
      }
      throw err;
    }
  }

  function renderDevices(devices) {
    if (!devices.length) {
      setTableMessage(elements.devicesTable, 6, 'Không có thiết bị nào');
      return;
    }

    elements.devicesTable.innerHTML = devices
      .map((device) => {
        const status = (device.status || '').toLowerCase();
        const statusClass = status === 'online' ? 'status-online' : 'status-offline';
        const actions = buildDeviceActions(device);

        return `
          <tr>
            <td>${escapeHtml(device.device_id)}</td>
            <td>${escapeHtml(device.gateway_id)}</td>
            <td>${escapeHtml(device.device_type)}</td>
            <td><span class="status-pill ${statusClass}">${status || 'unknown'}</span></td>
            <td>${formatDateTime(device.last_seen)}</td>
            <td>
              ${
                actions.length
                  ? `<div class="action-buttons">${actions.join('')}</div>`
                  : '<span class="muted">Không có hành động</span>'
              }
            </td>
          </tr>
        `;
      })
      .join('');
  }

  function buildDeviceActions(device) {
    const actions = [];
    const type = String(device.device_type || '').toLowerCase();
    const fanAllowed = isFeatureAllowed('fan');

    if (fanAllowed && type.includes('fan')) {
      actions.push(createActionButton('Bật', 'fan_on', device));
      actions.push(createActionButton('Tắt', 'fan_off', device));
    }

    if (type.includes('door') || type.includes('lock') || type.includes('gate')) {
      actions.push(createActionButton('Mở khóa', 'unlock', device));
      actions.push(createActionButton('Khóa', 'lock', device));
    }

    return actions;
  }

  function createActionButton(label, action, device) {
    return `<button data-action="${action}" data-device-id="${escapeHtml(
      device.device_id
    )}" data-gateway-id="${escapeHtml(device.gateway_id)}">${label}</button>`;
  }

  async function loadAccessLogs(isRefresh) {
    if (isRefresh) {
      setTableMessage(elements.accessTable, 5, 'Đang tải dữ liệu...');
    }

    try {
      const res = await apiFetch('/api/access/logs?limit=25');
      const rows = Array.isArray(res) ? res : res?.data || [];

      if (!rows.length) {
        setTableMessage(elements.accessTable, 5, 'Chưa có lượt vào/ra');
        return;
      }

      elements.accessTable.innerHTML = rows
        .map((row) => {
          const result = String(row.result || '').toLowerCase();
          const success = result === 'granted' || result === 'success';
          const method = formatMethod(row.method);
          const statusClass = success ? 'status-online' : 'status-offline';
          const statusLabel = success ? 'GRANTED' : 'DENIED';

          return `
            <tr>
              <td>${formatDateTime(row.time)}</td>
              <td>${escapeHtml(row.device_id)}</td>
              <td>${method}</td>
              <td><span class="status-pill ${statusClass}">${statusLabel}</span></td>
              <td>${escapeHtml(row.user_id)}</td>
            </tr>
          `;
        })
        .join('');
    } catch (err) {
      setTableMessage(elements.accessTable, 5, 'Không thể tải lịch sử vào/ra');
      throw err;
    }
  }

  async function loadRfidCards(isRefresh) {
    if (!elements.rfidTable) return;

    if (!isFeatureAllowed('rfid')) {
      rfidCardsCache = [];
      setFeatureVisibility(elements.rfidPanel, 'rfid');
      setTableMessage(elements.rfidTable, 5, 'Bạn không có quyền xem thẻ RFID');
      return;
    }

    if (isRefresh) {
      setTableMessage(elements.rfidTable, 5, 'Đang tải dữ liệu...');
    }

    try {
      const res = await apiFetch('/api/access/rfid');
      const rows = Array.isArray(res) ? res : res?.data || [];
      rfidCardsCache = rows;

      if (!rows.length) {
        setTableMessage(elements.rfidTable, 5, 'Chưa có thẻ RFID nào');
        return;
      }

      const canManage = isFeatureAllowed('rfid');

      elements.rfidTable.innerHTML = rows
        .map((card) => {
          const active = Boolean(card.active);
          const statusClass = active ? 'status-online' : 'status-offline';
          const statusLabel = active ? 'ĐANG HOẠT ĐỘNG' : 'NGỪNG';
          const actions = canManage
            ? `<div class="action-buttons">
                 <button data-action="edit" data-uid="${escapeHtml(card.uid)}">Sửa</button>
                 <button data-action="delete" data-uid="${escapeHtml(card.uid)}">Xoá</button>
               </div>`
            : '<span class="muted">Không có hành động</span>';

          return `
            <tr>
              <td>${escapeHtml(card.uid)}</td>
              <td>${escapeHtml(card.user_id)}</td>
              <td><span class="status-pill ${statusClass}">${statusLabel}</span></td>
              <td>${formatDateTime(card.updated_at || card.registered_at)}</td>
              <td>${actions}</td>
            </tr>
          `;
        })
        .join('');
    } catch (err) {
      setTableMessage(elements.rfidTable, 5, 'Không thể tải thẻ RFID');
      throw err;
    }
  }

  async function sendCommand(gatewayId, deviceId, action) {
    let endpoint = `/api/commands/${encodeURIComponent(
      gatewayId
    )}/${encodeURIComponent(deviceId)}`;

    const supportedActions = ['fan_on', 'fan_off', 'unlock', 'lock'];
    if (supportedActions.includes(action)) {
      endpoint += `/${action}`;
      const res = await apiFetch(endpoint, { method: 'POST' });
      if (res?.success === false) {
        throw new Error(res.detail || res.message || 'Thiết bị phản hồi thất bại');
      }
      return res;
    }

    const res = await apiFetch(endpoint, {
      method: 'POST',
      body: { command: action }
    });

    if (res?.success === false) {
      throw new Error(res.detail || res.message || 'Thiết bị phản hồi thất bại');
    }

    return res;
  }

  function setTableMessage(tbody, colSpan, message) {
    tbody.innerHTML = `<tr><td colspan="${colSpan}" class="empty-cell">${message}</td></tr>`;
  }

  function formatNumber(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) return '--';
    return Number(value).toLocaleString('vi-VN');
  }

  function formatDateTime(value) {
    if (!value) return '--';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '--';
    return date.toLocaleString('vi-VN', {
      hour12: false
    });
  }

  function formatTime(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString('vi-VN', {
      hour: '2-digit',
      minute: '2-digit',
      day: '2-digit',
      month: '2-digit'
    });
  }

  function formatMethod(method) {
    if (!method) return '--';
    const normalized = String(method).toLowerCase();
    switch (normalized) {
      case 'rfid':
        return 'RFID';
      case 'passkey':
        return 'Passkey';
      case 'remote':
        return 'Remote';
      default:
        return normalized.charAt(0).toUpperCase() + normalized.slice(1);
    }
  }

  function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    return String(value).replace(/[&<>"']/g, (char) => {
      switch (char) {
        case '&':
          return '&amp;';
        case '<':
          return '&lt;';
        case '>':
          return '&gt;';
        case '"':
          return '&quot;';
        case "'":
          return '&#39;';
        default:
          return char;
      }
    });
  }

  function showToast(type, message) {
    if (!elements.toast) return;

    if (toastTimer) {
      clearTimeout(toastTimer);
      toastTimer = null;
    }

    elements.toast.textContent = message;
    elements.toast.classList.remove('hidden', 'success', 'error', 'show');
    elements.toast.classList.add(type === 'error' ? 'error' : 'success');

    requestAnimationFrame(() => {
      elements.toast.classList.add('show');
    });

    toastTimer = setTimeout(() => {
      elements.toast.classList.remove('show');
    }, 3200);
  }

  function handleLogout() {
    authToken = null;
    currentUser = null;
    devicesCache = [];
    passkeysCache = [];
    rfidCardsCache = [];
    latestReadings = [];
    currentSensorId = null;
    destroyTemperatureChart();
    passkeyContext.deviceId = null;
    passkeyContext.gatewayId = null;
    passkeyBuffer = '';
    passkeySubmitting = false;
    resetPasskeyState(true);
    setPasskeyInputsDisabled(true);
    elements.passkeySection?.classList.add('no-device');
    setPasskeyStatus('Chưa có thiết bị passkey', 'info');

    localStorage.removeItem('iot_auth_token');

    elements.appHeader.classList.add('hidden');
    elements.appContent.classList.add('hidden');
    toggleLoginModal(true);
    updatePasskeyControls();
    updateRfidControls();
  }

  async function apiFetch(path, options = {}) {
    const baseUrl = String(window.API_URL || 'http://127.0.0.1:3000').replace(/\/$/, '');
    const url = `${baseUrl}${path}`;

    const headers = new Headers(options.headers || {});
    const init = {
      method: options.method || 'GET',
      headers,
      body: null
    };

    const shouldAttachAuth = options.auth !== false && authToken;
    if (shouldAttachAuth) {
      headers.set('Authorization', `Bearer ${authToken}`);
    }

    if (options.body !== undefined && options.body !== null) {
      if (options.body instanceof FormData) {
        init.body = options.body;
      } else if (typeof options.body === 'string') {
        init.body = options.body;
        if (!headers.has('Content-Type')) {
          headers.set('Content-Type', 'application/json');
        }
      } else {
        init.body = JSON.stringify(options.body);
        headers.set('Content-Type', 'application/json');
      }
    }

    const response = await fetch(url, init);
    const contentType = response.headers.get('content-type') || '';
    let payload = null;

    if (contentType.includes('application/json')) {
      payload = await response.json().catch(() => null);
    } else {
      const text = await response.text();
      if (text) {
        try {
          payload = JSON.parse(text);
        } catch {
          payload = text;
        }
      }
    }

    if (!response.ok) {
      if (response.status === 401 && shouldAttachAuth) {
        handleLogout();
      }
      const message =
        (payload && (payload.detail || payload.message)) ||
        `Yêu cầu thất bại (${response.status})`;
      const error = new Error(message);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }

    return payload;
  }
})();
