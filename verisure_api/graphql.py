"""GraphQL query and mutation definitions for the Verisure IT API.

These are the raw GraphQL strings sent to customers.verisure.it/owa-api/graphql.
Kept as named constants so they're easy to diff against upstream when Verisure
changes their API.

Operation names MUST match what the API expects — they're sent in headers
and used for routing.
"""

# --- Authentication ---

LOGIN_TOKEN_MUTATION = (
    "mutation mkLoginToken("
    "$user: String!, $password: String!, $id: String!, $country: String!, "
    "$lang: String!, $callby: String!, $idDevice: String!, "
    "$idDeviceIndigitall: String!, $deviceType: String!, $deviceVersion: String!, "
    "$deviceResolution: String!, $deviceName: String!, $deviceBrand: String!, "
    "$deviceOsVersion: String!, $uuid: String!"
    ") { xSLoginToken("
    "user: $user, password: $password, country: $country, lang: $lang, "
    "callby: $callby, id: $id, idDevice: $idDevice, "
    "idDeviceIndigitall: $idDeviceIndigitall, deviceType: $deviceType, "
    "deviceVersion: $deviceVersion, deviceResolution: $deviceResolution, "
    "deviceName: $deviceName, deviceBrand: $deviceBrand, "
    "deviceOsVersion: $deviceOsVersion, uuid: $uuid"
    ") { __typename res msg hash refreshToken legals changePassword "
    "needDeviceAuthorization mainUser } }"
)

VALIDATE_DEVICE_MUTATION = (
    "mutation mkValidateDevice("
    "$idDevice: String, $idDeviceIndigitall: String, $uuid: String, "
    "$deviceName: String, $deviceBrand: String, $deviceOsVersion: String, "
    "$deviceVersion: String"
    ") { xSValidateDevice("
    "idDevice: $idDevice, idDeviceIndigitall: $idDeviceIndigitall, "
    "uuid: $uuid, deviceName: $deviceName, deviceBrand: $deviceBrand, "
    "deviceOsVersion: $deviceOsVersion, deviceVersion: $deviceVersion"
    ") { res msg hash refreshToken legals } }"
)

SEND_OTP_MUTATION = (
    "mutation mkSendOTP($recordId: Int!, $otpHash: String!) {"
    " xSSendOtp(recordId: $recordId, otpHash: $otpHash) { res msg } }"
)

LOGOUT_MUTATION = "mutation Logout { xSLogout }"

# --- Installation & Services ---

INSTALLATION_LIST_QUERY = (
    "query mkInstallationList { xSInstallations { installations {"
    " numinst alias panel type name surname address city postcode"
    " province email phone } } }"
)

SERVICES_QUERY = (
    "query Srv($numinst: String!, $uuid: String) {"
    " xSSrv(numinst: $numinst, uuid: $uuid) {"
    " res msg language installation {"
    " numinst role alias status panel sim instIbs"
    " services {"
    " idService active visible bde isPremium codOper request"
    " minWrapperVersion description"
    " attributes { attributes { name value active } }"
    " }"
    " capabilities"
    " } } }"
)

# --- Alarm Status ---

CHECK_ALARM_QUERY = (
    "query CheckAlarm($numinst: String!, $panel: String!) {"
    " xSCheckAlarm(numinst: $numinst, panel: $panel) {"
    " res msg referenceId } }"
)

CHECK_ALARM_STATUS_QUERY = (
    "query CheckAlarmStatus("
    "$numinst: String!, $idService: String!, $panel: String!, $referenceId: String!"
    ") { xSCheckAlarmStatus("
    "numinst: $numinst, idService: $idService, panel: $panel, "
    "referenceId: $referenceId"
    ") { res msg status numinst protomResponse protomResponseDate } }"
)

GENERAL_STATUS_QUERY = (
    "query Status($numinst: String!) {"
    " xSStatus(numinst: $numinst) {"
    " status timestampUpdate"
    " exceptions { status deviceType alias } } }"
)

# --- Arm / Disarm ---

ARM_PANEL_MUTATION = (
    "mutation xSArmPanel("
    "$numinst: String!, $request: ArmCodeRequest!, $panel: String!, "
    "$currentStatus: String, $suid: String, "
    "$forceArmingRemoteId: String"
    ") { xSArmPanel("
    "numinst: $numinst, request: $request, panel: $panel, "
    "currentStatus: $currentStatus, suid: $suid, "
    "forceArmingRemoteId: $forceArmingRemoteId"
    ") { res msg referenceId } }"
)

ARM_STATUS_QUERY = (
    "query ArmStatus("
    "$numinst: String!, $request: ArmCodeRequest, $panel: String!, "
    "$referenceId: String!, $counter: Int!, "
    "$forceArmingRemoteId: String"
    ") { xSArmStatus("
    "numinst: $numinst, panel: $panel, referenceId: $referenceId, "
    "counter: $counter, request: $request, "
    "forceArmingRemoteId: $forceArmingRemoteId"
    ") { res msg status protomResponse protomResponseDate numinst requestId"
    " error { code type allowForcing exceptionsNumber referenceId suid } } }"
)

GET_EXCEPTIONS_QUERY = (
    "query xSGetExceptions("
    "$numinst: String!, $panel: String!, "
    "$referenceId: String!, $counter: Int!, $suid: String"
    ") { xSGetExceptions("
    "numinst: $numinst, panel: $panel, "
    "referenceId: $referenceId, counter: $counter, suid: $suid"
    ") { res msg"
    " exceptions { status deviceType alias } } }"
)

DISARM_PANEL_MUTATION = (
    "mutation xSDisarmPanel("
    "$numinst: String!, $request: DisarmCodeRequest!, $panel: String!"
    ") { xSDisarmPanel("
    "numinst: $numinst, request: $request, panel: $panel"
    ") { res msg referenceId } }"
)

DISARM_STATUS_QUERY = (
    "query DisarmStatus("
    "$numinst: String!, $panel: String!, $referenceId: String!, "
    "$counter: Int!, $request: DisarmCodeRequest"
    ") { xSDisarmStatus("
    "numinst: $numinst, panel: $panel, referenceId: $referenceId, "
    "counter: $counter, request: $request"
    ") { res msg status protomResponse protomResponseDate numinst requestId"
    " error { code type allowForcing exceptionsNumber referenceId } } }"
)
