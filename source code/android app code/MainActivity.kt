package com.example.bluetoothapp1

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.bluetooth.le.AdvertiseCallback
import android.bluetooth.le.AdvertiseData
import android.bluetooth.le.AdvertiseSettings
import android.bluetooth.le.BluetoothLeAdvertiser
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import kotlinx.coroutines.*
import java.nio.ByteBuffer
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

private const val TAG = "BleCounter"

class MainActivity : ComponentActivity() {

    private lateinit var bleAdvertiser: BleCounterAdvertiser

    private val bluetoothPermissions: Array<String> by lazy {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            arrayOf(
                Manifest.permission.BLUETOOTH_ADVERTISE,
                Manifest.permission.BLUETOOTH_SCAN,
                Manifest.permission.BLUETOOTH_CONNECT
            )
        } else {
            arrayOf(
                Manifest.permission.BLUETOOTH,
                Manifest.permission.BLUETOOTH_ADMIN
            )
        }
    }

    private val permissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { results ->
            val allGranted = results.values.all { it }
            Log.d(TAG, "Permissions result, allGranted = $allGranted")
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        bleAdvertiser = BleCounterAdvertiser(this)

        setContent {
            MaterialTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background
                ) {
                    BleCounterScreen(
                        bleAdvertiser = bleAdvertiser,
                        onRequestPermissions = { requestBluetoothPermissions() }
                    )
                }
            }
        }
    }

    private fun requestBluetoothPermissions() {
        val notGranted = bluetoothPermissions.filter { perm ->
            ContextCompat.checkSelfPermission(this, perm) != PackageManager.PERMISSION_GRANTED
        }
        if (notGranted.isNotEmpty()) {
            permissionLauncher.launch(notGranted.toTypedArray())
        } else {
            Log.d(TAG, "All BT perms already granted")
        }
    }
}

/*=====================================================
    BLE Advertiser Backend
======================================================*/

class BleCounterAdvertiser(private val context: Context) {

    private val bluetoothManager: BluetoothManager? =
        context.getSystemService(BluetoothManager::class.java)
    private val bluetoothAdapter: BluetoothAdapter? = bluetoothManager?.adapter
    private val advertiser: BluetoothLeAdvertiser? = bluetoothAdapter?.bluetoothLeAdvertiser

    private val manufacturerId: Int = 0xFFFF

    private var advertiseJob: Job? = null
    private var counterValue: Int = 0

    private var _isAdvertising = mutableStateOf(false)
    val isAdvertising: State<Boolean> get() = _isAdvertising

    private var _lastCounter = mutableStateOf(0)
    val lastCounter: State<Int> get() = _lastCounter

    private var _statusMessage = mutableStateOf("")
    val statusMessage: State<String> get() = _statusMessage

    private var _lastModeLabel = mutableStateOf("")
    val lastModeLabel: State<String> get() = _lastModeLabel

    private var _lastTxLabel = mutableStateOf("")
    val lastTxLabel: State<String> get() = _lastTxLabel

    private val _logs = mutableStateListOf<String>()
    val logs: List<String> get() = _logs

    private val timeFormat = SimpleDateFormat("HH:mm:ss.SSS", Locale.getDefault())

    private val advertiseCallback = object : AdvertiseCallback() {
        override fun onStartSuccess(settingsInEffect: AdvertiseSettings?) {
            Log.d(TAG, "Advertising started successfully")
        }

        override fun onStartFailure(errorCode: Int) {
            Log.e(TAG, "Advertising failed: $errorCode")
            appendLog("ERROR: start failed, code=$errorCode")
            _statusMessage.value = "Start failed: $errorCode"
        }
    }

    fun isSupported(): Boolean {
        return bluetoothAdapter != null &&
                bluetoothAdapter.isEnabled &&
                advertiser != null
    }

    private fun hasAdvertisePermission(): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.BLUETOOTH_ADVERTISE
            ) == PackageManager.PERMISSION_GRANTED
        } else {
            ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.BLUETOOTH
            ) == PackageManager.PERMISSION_GRANTED
        }
    }

    private fun appendLog(line: String) {
        if (_logs.size > 500) {
            _logs.removeAt(0)
        }
        _logs.add(line)
        Log.d(TAG, line)
    }

    fun clearLogs() {
        _logs.clear()
    }

    fun copyLogsToClipboard() {
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        val text = _logs.joinToString("\n")
        val clip = ClipData.newPlainText("BLE Logs", text)
        clipboard.setPrimaryClip(clip)
        appendLog("Logs copied to clipboard")
    }

    /*=====================================================
        NEW Start() — No custom interval, interval tied to Advertise Mode
    ======================================================*/
    @SuppressLint("MissingPermission")
    fun start(
        txPowerLevel: Int,
        advertiseMode: Int,
        modeLabel: String,
        txLabel: String
    ) {
        if (!isSupported()) {
            appendLog("ERROR: BLE not supported")
            _statusMessage.value = "BLE not supported"
            return
        }

        if (!hasAdvertisePermission()) {
            appendLog("ERROR: Missing BLUETOOTH_ADVERTISE permission")
            _statusMessage.value = "Missing BLUETOOTH_ADVERTISE"
            return
        }

        stop()

        _isAdvertising.value = true
        _lastModeLabel.value = modeLabel
        _lastTxLabel.value = txLabel
        _statusMessage.value = "Advertising..."

        appendLog("=== Start advertising (mode=$modeLabel, tx=$txLabel) ===")

        // Actual BLE TX interval (approx) determined ONLY by Advertise Mode
        val loopDelayMs = when (advertiseMode) {
            AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY -> 20L
            AdvertiseSettings.ADVERTISE_MODE_BALANCED -> 250L
            AdvertiseSettings.ADVERTISE_MODE_LOW_POWER -> 1000L
            else -> 250L
        }

        val scope = CoroutineScope(Dispatchers.Default)
        advertiseJob = scope.launch {

            while (isActive) {
                val counter = counterValue
                val txUnixMs = System.currentTimeMillis()
                val wall = timeFormat.format(Date(txUnixMs))

                // Payload: counter (2 bytes) + power code (1) + unix64 (8)
                val buf = ByteBuffer.allocate(11)
                buf.putShort(counter.toShort())
                buf.put(txPowerLevel.toByte())
                buf.putLong(txUnixMs)
                val payload = buf.array()

                // Settings
                val settings = AdvertiseSettings.Builder()
                    .setAdvertiseMode(advertiseMode)
                    .setTxPowerLevel(txPowerLevel)
                    .setConnectable(false)
                    .build()

                val data = AdvertiseData.Builder()
                    .setIncludeDeviceName(true)
                    .setIncludeTxPowerLevel(true)
                    .addManufacturerData(manufacturerId, payload)
                    .build()

                try {
                    advertiser?.startAdvertising(settings, data, advertiseCallback)
                } catch (e: Exception) {
                    appendLog("ERROR: startAdvertising failed: ${e.message}")
                    break
                }

                _lastCounter.value = counter
                appendLog("TX #$counter | wall=$wall | unix=$txUnixMs | mode=$modeLabel | tx=$txLabel")

                delay(loopDelayMs)

                try {
                    advertiser?.stopAdvertising(advertiseCallback)
                } catch (_: Exception) {}

                counterValue++
            }

            _isAdvertising.value = false
            appendLog("=== Advertising ended ===")
        }
    }

    @SuppressLint("MissingPermission")
    fun stop() {
        advertiseJob?.cancel()
        advertiseJob = null
        try {
            advertiser?.stopAdvertising(advertiseCallback)
        } catch (_: Exception) {}
        _isAdvertising.value = false
        _statusMessage.value = "Stopped"
        appendLog("Stopped advertising")
    }

    fun resetCounter() {
        counterValue = 0
        _lastCounter.value = 0
        appendLog("Counter reset to 0")
    }
}

/*=====================================================
    UI
======================================================*/

@Composable
fun BleCounterScreen(
    bleAdvertiser: BleCounterAdvertiser,
    onRequestPermissions: () -> Unit
) {
    val isSupported = bleAdvertiser.isSupported()
    val isAdvertising by bleAdvertiser.isAdvertising
    val lastCounter by bleAdvertiser.lastCounter
    val status by bleAdvertiser.statusMessage
    val lastMode by bleAdvertiser.lastModeLabel
    val lastTx by bleAdvertiser.lastTxLabel
    val logs = bleAdvertiser.logs

    val modeOptions = listOf(
        "LOW_POWER" to AdvertiseSettings.ADVERTISE_MODE_LOW_POWER,
        "BALANCED" to AdvertiseSettings.ADVERTISE_MODE_BALANCED,
        "LOW_LATENCY" to AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY
    )
    var selectedModeIndex by remember { mutableStateOf(1) }

    val txPowerOptions = listOf(
        "ULTRA_LOW" to AdvertiseSettings.ADVERTISE_TX_POWER_ULTRA_LOW,
        "LOW" to AdvertiseSettings.ADVERTISE_TX_POWER_LOW,
        "MEDIUM" to AdvertiseSettings.ADVERTISE_TX_POWER_MEDIUM,
        "HIGH" to AdvertiseSettings.ADVERTISE_TX_POWER_HIGH
    )
    var selectedTxIndex by remember { mutableStateOf(3) }

    val selectedMode = modeOptions[selectedModeIndex]
    val selectedTx = txPowerOptions[selectedTxIndex]

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Text("BLE Counter Advertiser", fontSize = 22.sp, fontWeight = FontWeight.Bold)

        if (!isSupported) {
            Text(
                "Bluetooth not supported or OFF",
                color = MaterialTheme.colorScheme.error
            )
        }

        Text("Advertise Mode:")
        Row {
            modeOptions.forEachIndexed { index, (label, _) ->
                FilterChip(
                    selected = index == selectedModeIndex,
                    onClick = { selectedModeIndex = index },
                    label = { Text(label) }
                )
                Spacer(Modifier.width(6.dp))
            }
        }

        Text("Tx Power Level:")
        Row {
            txPowerOptions.forEachIndexed { index, (label, _) ->
                FilterChip(
                    selected = index == selectedTxIndex,
                    onClick = { selectedTxIndex = index },
                    label = { Text(label) }
                )
                Spacer(Modifier.width(6.dp))
            }
        }

        Text("Last counter: $lastCounter")
        if (lastMode.isNotEmpty()) {
            Text("Last settings: mode=$lastMode, tx=$lastTx")
        }
        Text("Status: $status")

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Button(onClick = { onRequestPermissions() }) { Text("Request permissions") }

            Button(
                onClick = {
                    bleAdvertiser.start(
                        txPowerLevel = selectedTx.second,
                        advertiseMode = selectedMode.second,
                        modeLabel = selectedMode.first,
                        txLabel = selectedTx.first
                    )
                },
                enabled = !isAdvertising && isSupported
            ) { Text("Start") }

            OutlinedButton(onClick = { bleAdvertiser.copyLogsToClipboard() }) {
                Text("Copy logs")
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            OutlinedButton(onClick = { bleAdvertiser.stop() }, enabled = isAdvertising) {
                Text("Stop")
            }
            OutlinedButton(onClick = { bleAdvertiser.resetCounter() }) {
                Text("Reset counter")
            }
            OutlinedButton(onClick = { bleAdvertiser.clearLogs() }) {
                Text("Clear logs")
            }
        }

        Text("Logs:", fontWeight = FontWeight.Bold)

        Box(
            modifier = Modifier
                .fillMaxWidth()
                .weight(1f)
        ) {
            val scrollState = rememberScrollState()
            Column(
                modifier = Modifier
                    .verticalScroll(scrollState)
            ) {
                logs.forEach {
                    Text(it, fontSize = 12.sp)
                }
            }
        }
    }
}