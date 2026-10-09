; 공작기계지능화실험실 제조 DB — 배포용 설치 프로그램 (Inno Setup 6.7)
;
; installer\build_installer.py 가 build\installer\stage 를 채운 뒤 이 파일을 컴파일한다.
; 직접 컴파일하지 말고 빌드 스크립트를 쓴다 (런타임·MySQL·공구 목록을 함께 준비해야 한다).
;
; 이 스크립트가 맡는 것: 마법사 화면(데이터 위치 · MySQL 선택과 연결 확인), 파일 복사, VC++ 런타임,
; 방화벽, 바로 가기, 업그레이드 전 서비스 정지, 제거. MySQL 초기화·서비스 등록·DB·테이블·공구 목록은
; 설치된 파이썬 런타임으로 installer\setup_target.py 를 단계마다 불러 처리한다(로그: {app}\logs\install.log).
;
; 시험용 판(/DTestMode, build_installer.py --test): 관리자 권한 없이 설치·제거되며 서비스 대신 mysqld 를
; 백그라운드로 띄우고, VC++ 런타임 · 방화벽 · 바로 가기는 건너뛴다. 설치 흐름의 나머지는 같다.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef StageDir
  #define StageDir AddBackslash(SourcePath) + "..\build\installer\stage"
#endif
#ifndef MySQLVersion
  #define MySQLVersion "8.0"
#endif
#ifndef VCRedistMajor
  #define VCRedistMajor "14"
  #define VCRedistMinor "0"
  #define VCRedistBld "0"
#endif

#define AppName "공작기계지능화실험실 제조 DB"
#define AppPublisher "Machine Tool Intelligence Lab"
#define ControllerExe "system_controller.exe"
#define ControllerName "제조 DB Controller"
#define ServiceName "OrcusMySQL"
#define DashboardPort "8501"
#define FirewallRule "Orcus Manufacturing DB Dashboard (TCP 8501)"
#define ApiPort "8000"
#define FirewallRuleApi "Orcus Manufacturing DB REST API (TCP 8000)"
#define DefaultPassword "610610610"

#ifdef TestMode
  #define AppIdGuid "{{FF9566DC-2FC4-4E89-AE10-0D2C88626549}"
  #define NameSuffix " (시험용)"
  #define FileSuffix "_TEST"
#else
  #define AppIdGuid "{{2EEA73A5-1F37-40DC-ABAF-F53873E79D93}"
  #define NameSuffix ""
  #define FileSuffix ""
#endif

[Setup]
AppId={#AppIdGuid}
AppName={#AppName}{#NameSuffix}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}{#NameSuffix}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}.0
VersionInfoDescription={#AppName} 설치 프로그램
DefaultDirName={code:DefaultInstallDir}
DisableProgramGroupPage=yes
DefaultGroupName={#AppName}
UsePreviousAppDir=yes
DisableDirPage=auto
AlwaysShowDirOnReadyPage=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
#ifdef TestMode
PrivilegesRequired=lowest
#else
PrivilegesRequired=admin
#endif
OutputBaseFilename=Setup_ManufacturingDB_v{#AppVersion}{#FileSuffix}
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
LZMANumBlockThreads=4
SetupIconFile={#StageDir}\app\assets\logo.ico
UninstallDisplayIcon={app}\{#ControllerExe}
UninstallDisplayName={#AppName}{#NameSuffix}
WizardStyle=modern
WizardSizePercent=120
InfoBeforeFile=install_info.txt
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
ShowLanguageDialog=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Messages]
FinishedLabel=[name] 설치를 마쳤습니다.%n%n컨트롤러에서 [전체 시작] 을 누르면 웹 대시보드가 http://localhost:{#DashboardPort} 에서 열립니다. 새로 가입한 계정은 컨트롤러의 [유저 관리] 에서 승인합니다.
FinishedLabelNoIcons=[name] 설치를 마쳤습니다.%n%n컨트롤러에서 [전체 시작] 을 누르면 웹 대시보드가 http://localhost:{#DashboardPort} 에서 열립니다. 새로 가입한 계정은 컨트롤러의 [유저 관리] 에서 승인합니다.

[Tasks]
#ifndef TestMode
Name: "desktopicon"; Description: "바탕 화면에 '{#ControllerName}' 바로 가기 만들기"; GroupDescription: "추가 작업:"
Name: "firewall"; Description: "다른 PC 에서 웹 대시보드(포트 {#DashboardPort})에 접속할 수 있게 Windows 방화벽 열기"; GroupDescription: "추가 작업:"
; REST API 는 로그인 없이 DB 전체를 읽고 내보낼 수 있어 기본으로는 열지 않는다
Name: "firewallapi"; Description: "다른 PC 에서 REST API(포트 {#ApiPort})에 접속할 수 있게 방화벽 열기 (로그인 없이 DB 를 읽을 수 있으니 믿을 수 있는 네트워크에서만)"; GroupDescription: "추가 작업:"; Flags: unchecked
#endif

[Dirs]
#ifndef TestMode
; 컨트롤러·수집기는 설치한 관리자가 아니라 평소 사용자 계정으로 돈다. 설치 폴더(data · 설정 · 로그)와
; 데이터 폴더(원본 백업 보관소)에 쓸 수 있어야 한다.
Name: "{app}"; Permissions: users-modify
Name: "{code:GetDataRoot}"; Permissions: users-modify; Flags: uninsneveruninstall
#endif

[Files]
; 기존 MySQL 연결 확인용. 설치 전에 임시 폴더로만 풀어 쓴다 (솔리드 압축이라 목록 맨 앞에 둔다).
Source: "{#StageDir}\mysql\bin\mysql.exe"; Flags: dontcopy
Source: "{#StageDir}\mysql\bin\libcrypto-3-x64.dll"; Flags: dontcopy
Source: "{#StageDir}\mysql\bin\libssl-3-x64.dll"; Flags: dontcopy
#ifndef TestMode
Source: "{#StageDir}\redist\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall
#endif
Source: "{#StageDir}\app\*"; DestDir: "{app}"; Excludes: "\backend\pipeline_config.json"; Flags: ignoreversion recursesubdirs createallsubdirs
; 파서 On/Off 는 컨트롤러가 이 파일에 저장한다. 업그레이드할 때 사용자가 바꾼 값을 덮어쓰지 않는다.
Source: "{#StageDir}\app\backend\pipeline_config.json"; DestDir: "{app}\backend"; Flags: onlyifdoesntexist
Source: "{#StageDir}\runtime\*"; DestDir: "{app}\runtime"; Excludes: ".build-stamp"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\mysql\*"; DestDir: "{app}\mysql"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; 업그레이드: 새 판에서 빠진 패키지가 예전 판의 것으로 남아 섞이지 않게 패키지 폴더를 비우고 다시 넣는다
Type: filesandordirs; Name: "{app}\runtime\Lib\site-packages"

[Icons]
#ifndef TestMode
Name: "{autoprograms}\{#AppName}\{#ControllerName}"; Filename: "{app}\{#ControllerExe}"; WorkingDir: "{app}"
Name: "{autoprograms}\{#AppName}\설치 폴더 열기"; Filename: "{app}"
Name: "{autoprograms}\{#AppName}\설치 기록 (install.log)"; Filename: "{app}\logs\install.log"
Name: "{autoprograms}\{#AppName}\{#AppName} 제거"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#ControllerName}"; Filename: "{app}\{#ControllerExe}"; WorkingDir: "{app}"; Tasks: desktopicon
#endif

[Run]
Filename: "{app}\{#ControllerExe}"; WorkingDir: "{app}"; Description: "{#ControllerName} 실행"; Flags: postinstall nowait skipifsilent runasoriginaluser; Check: ConfigSucceeded

[UninstallDelete]
; 설치 뒤에 생긴 것 중 프로그램의 일부인 것만 지운다. data\ · logs\ · .env 는 남긴다.
Type: filesandordirs; Name: "{app}\runtime"
Type: filesandordirs; Name: "{app}\mysql"
Type: filesandordirs; Name: "{app}\backend\__pycache__"
Type: filesandordirs; Name: "{app}\backend\DB\__pycache__"
Type: filesandordirs; Name: "{app}\backend\parsers\__pycache__"
Type: filesandordirs; Name: "{app}\frontend\__pycache__"
Type: filesandordirs; Name: "{app}\frontend\components\__pycache__"
Type: filesandordirs; Name: "{app}\installer\__pycache__"
Type: files; Name: "{app}\installer\install_state.ini"

[Code]
const
  SERVICE_NAME = '{#ServiceName}';
  DEFAULT_PASSWORD = '{#DefaultPassword}';
  { setup_target.PASSWORD_CHARS 와 같아야 한다 }
  PASSWORD_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!#$^&*()-_=+[]{};:,.?~';
  DRIVE_FIXED = 3;
  STATUS_DLL_NOT_FOUND = -1073741515;
  EXIT_AUTH = 2;
  CONFIG_FAILED_EXIT_CODE = 8;

var
  DataPage: TInputDirWizardPage;
  DbPage: TWizardPage;
  RbBundled, RbExisting: TNewRadioButton;
  EdBundledPort, EdExistingPort: TNewEdit;
  EdPassword: TPasswordEdit;
  LblDetect, LblBundledPort, LblExistingPort, LblBundledNote, LblExistingNote: TNewStaticText;
  LblPassword, LblPasswordHint: TNewStaticText;
  DetectedServices: String;
  Port3306Busy, OurServiceExists, UpgradeMode, ConfigFailed, VCRedistReboot: Boolean;
  ExistingDbChecked: String;

function GetDriveType(lpRootPathName: String): Cardinal;
  external 'GetDriveTypeW@kernel32.dll stdcall';

{ ------------------------------------------------------------------ 공통 }

function Q(const S: String): String;
begin
  Result := '"' + S + '"';
end;

function HasFixedDriveD: Boolean;
begin
  Result := GetDriveType('D:\') = DRIVE_FIXED;
end;

function DefaultInstallDir(Param: String): String;
begin
  if HasFixedDriveD then
    Result := 'D:\ManufacturingDB'
  else
    Result := ExpandConstant('{sd}') + '\ManufacturingDB';
end;

function StateFile: String;
begin
  Result := ExpandConstant('{app}\installer\install_state.ini');
end;

{ 같은 폴더에 이미 설정까지 끝난 설치가 있으면 업그레이드다 (설정 화면을 건너뛰고 기존 설정을 그대로 쓴다) }
function IsUpgradeInstall: Boolean;
begin
  Result := FileExists(StateFile) and FileExists(ExpandConstant('{app}\.env'));
end;

function GetDataRoot(Param: String): String;
begin
  if UpgradeMode then
    Result := GetIniString('paths', 'data_root', '', StateFile)
  else
    Result := RemoveBackslashUnlessRoot(Trim(DataPage.Values[0]));
  if Result = '' then
    Result := ExpandConstant('{app}\data');
end;

function IsPlainAsciiPath(const S: String): Boolean;
var
  I: Integer;
begin
  Result := Length(S) >= 3;
  for I := 1 to Length(S) do
    if (Ord(S[I]) < 32) or (Ord(S[I]) > 126) or (S[I] = '"') or (S[I] = '''') or (S[I] = '%') then
    begin
      Result := False;
      Exit;
    end;
end;

function IsInside(const Child, Parent: String): Boolean;
var
  C, P: String;
begin
  C := Lowercase(AddBackslash(Child));
  P := Lowercase(AddBackslash(Parent));
  Result := Copy(C, 1, Length(P)) = P;
end;

function BadPasswordChars(const S: String): String;
var
  I: Integer;
begin
  Result := '';
  for I := 1 to Length(S) do
    if (Pos(S[I], PASSWORD_CHARS) = 0) and (Pos(S[I], Result) = 0) then
      Result := Result + S[I];
end;

function PortValue(const S: String): Integer;
begin
  Result := StrToIntDef(Trim(S), 0);
  if (Result < 1024) or (Result > 65535) then
    Result := 0;
end;

function PowerShellExe: String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

{ 그 포트를 누가 듣고 있는지. PowerShell 을 못 쓰는 PC 에서는 '비어 있음'으로 보고, 실제 충돌은
  설치 마무리 단계(setup_target.py)가 다시 확인한다. }
function PortInUse(Port: Integer): Boolean;
var
  Code: Integer;
begin
  Result := False;
  if Exec(PowerShellExe, '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -State Listen -LocalPort ' +
          IntToStr(Port) + ' -ErrorAction SilentlyContinue) { exit 1 } else { exit 0 }"',
          '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Result := Code = 1
  else
    Log('포트 확인용 PowerShell 을 실행하지 못했습니다: ' + SysErrorMessage(Code));
  Log(Format('포트 %d 사용 중: %d', [Port, Ord(Result)]));
end;

function FirstFreePort(Start: Integer): Integer;
var
  P: Integer;
begin
  Result := Start;
  for P := Start to Start + 20 do
    if not PortInUse(P) then
    begin
      Result := P;
      Exit;
    end;
end;

procedure DetectMySQL;
var
  Names: TArrayOfString;
  I: Integer;
  N: String;
begin
  DetectedServices := '';
  if RegGetSubkeyNames(HKLM, 'SYSTEM\CurrentControlSet\Services', Names) then
    for I := 0 to GetArrayLength(Names) - 1 do
    begin
      N := Lowercase(Names[I]);
      if ((Pos('mysql', N) > 0) or (Pos('mariadb', N) > 0)) and (N <> Lowercase(SERVICE_NAME)) then
      begin
        if DetectedServices <> '' then
          DetectedServices := DetectedServices + ', ';
        DetectedServices := DetectedServices + Names[I];
      end;
    end;
  OurServiceExists := RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\' + SERVICE_NAME);
  Port3306Busy := PortInUse(3306);
  Log('감지: MySQL 서비스 [' + DetectedServices + '], OrcusMySQL ' + IntToStr(Ord(OurServiceExists)) +
      ', 3306 사용 중 ' + IntToStr(Ord(Port3306Busy)));
end;

function JsonEscape(const S: String): String;
var
  I: Integer;
  C: String;
begin
  Result := '';
  for I := 1 to Length(S) do
  begin
    C := S[I];
    if C = '\' then
      Result := Result + '\\'
    else if C = '"' then
      Result := Result + '\"'
    else if Ord(S[I]) < 32 then
      Result := Result + ' '
    else
      Result := Result + C;
  end;
end;

{ ------------------------------------------------------------------ 기존 MySQL 연결 확인 }

{ 0 접속됨(Version) · 1 비밀번호 틀림 · 2 연결 안 됨 · 3 확인할 수 없음(Detail) }
function ProbeExistingMySQL(Port: Integer; const Password: String; var Version, OrcusTables, Detail: String): Integer;
var
  Cnf, Exe, DbName: String;
  Output: TExecOutput;
  Code, I: Integer;
  Err: String;
  Ran: Boolean;
begin
  Result := 3;
  Version := '';
  OrcusTables := '';
  Detail := '';
  try
    ExtractTemporaryFile('mysql.exe');
    ExtractTemporaryFile('libcrypto-3-x64.dll');
    ExtractTemporaryFile('libssl-3-x64.dll');
  except
    Detail := GetExceptionMessage;
    Exit;
  end;
  Exe := ExpandConstant('{tmp}\mysql.exe');
  Cnf := ExpandConstant('{tmp}\orcus_probe.cnf');
  DbName := ExpandConstant('{param:DBNAME|orcus}');
  { 비밀번호를 명령줄에 싣지 않으려고 옵션 파일로 넘기고, 확인이 끝나면 바로 지운다 }
  SaveStringToFile(Cnf, '[client]' + #13#10 + 'user=root' + #13#10 + 'password="' + Password + '"' + #13#10 +
                   'host=localhost' + #13#10 + 'protocol=TCP' + #13#10 + 'port=' + IntToStr(Port) + #13#10 +
                   'connect-timeout=5' + #13#10, False);
  try
    try
      Ran := ExecAndCaptureOutput(Exe, '--defaults-file=' + Q(Cnf) + ' --batch --skip-column-names -e ' +
          Q('SELECT VERSION(); SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=''' + DbName + ''''),
          '', SW_SHOWNORMAL, ewWaitUntilTerminated, Code, Output);
      if not Ran then
        Detail := SysErrorMessage(Code);
    except
      Ran := False;
      Detail := GetExceptionMessage;
    end;
  finally
    DeleteFile(Cnf);
  end;
  if not Ran then
    Exit;
  Err := '';
  for I := 0 to GetArrayLength(Output.StdErr) - 1 do
    Err := Err + Output.StdErr[I] + ' ';
  Log(Format('기존 MySQL 확인: 코드 %d, 출력 %d줄, 오류 [%s]', [Code, GetArrayLength(Output.StdOut), Trim(Err)]));
  if Code = 0 then
  begin
    if GetArrayLength(Output.StdOut) > 0 then
      Version := Trim(Output.StdOut[0]);
    if GetArrayLength(Output.StdOut) > 1 then
      OrcusTables := Trim(Output.StdOut[1]);
    Result := 0;
  end
  else if Pos('ERROR 1045', Err) > 0 then
    Result := 1
  else if (Pos('ERROR 2003', Err) > 0) or (Pos('ERROR 2013', Err) > 0) then
    Result := 2
  else if Code = STATUS_DLL_NOT_FOUND then
    Detail := 'Visual C++ 런타임이 없어 확인 프로그램을 실행할 수 없습니다'
  else
    Detail := Trim(Err);
end;

{ 8.0.16 이상의 MySQL 인가 (CHECK 제약을 실제로 검사하는 첫 판). MariaDB 는 받지 않는다. }
function MySQLVersionOk(const V: String): Boolean;
var
  S: String;
  Major, Minor, Patch, P: Integer;
begin
  Result := False;
  if Pos('mariadb', Lowercase(V)) > 0 then
    Exit;
  S := V;
  P := Pos('.', S);
  if P = 0 then Exit;
  Major := StrToIntDef(Copy(S, 1, P - 1), 0);
  S := Copy(S, P + 1, Length(S));
  P := Pos('.', S);
  if P = 0 then Exit;
  Minor := StrToIntDef(Copy(S, 1, P - 1), 0);
  S := Copy(S, P + 1, Length(S));
  P := 1;
  while (P <= Length(S)) and (S[P] >= '0') and (S[P] <= '9') do
    P := P + 1;
  Patch := StrToIntDef(Copy(S, 1, P - 1), 0);
  Result := (Major > 8) or ((Major = 8) and ((Minor > 0) or (Patch >= 16)));
end;

{ ------------------------------------------------------------------ 마법사 화면 }

procedure UpdateDbControls;
begin
  EdBundledPort.Enabled := RbBundled.Checked;
  LblBundledPort.Enabled := RbBundled.Checked;
  EdExistingPort.Enabled := RbExisting.Checked;
  LblExistingPort.Enabled := RbExisting.Checked;
  if RbBundled.Checked then
    LblPassword.Caption := '새로 정할 MySQL root 비밀번호:'
  else
    LblPassword.Caption := '이 PC 에 있는 MySQL 의 root 비밀번호:';
end;

procedure DbChoiceClick(Sender: TObject);
begin
  UpdateDbControls;
end;

function AddLabel(Page: TWizardPage; const Caption: String; Left, Top, Width: Integer; Wrap: Boolean): TNewStaticText;
begin
  Result := TNewStaticText.Create(Page);
  Result.Parent := Page.Surface;
  Result.Left := Left;
  Result.Top := Top;
  Result.Width := Width;
  Result.WordWrap := Wrap;
  Result.AutoSize := True;
  Result.Caption := Caption;
end;

function AddEdit(Page: TWizardPage; const Text: String; Left, Top, Width: Integer): TNewEdit;
begin
  Result := TNewEdit.Create(Page);
  Result.Parent := Page.Surface;
  Result.Left := Left;
  Result.Top := Top;
  Result.Width := Width;
  Result.Text := Text;
end;

procedure CreateDbPage;
var
  W, Y, Indent: Integer;
  Mode, Pw, BundledPort: String;
begin
  DbPage := CreateCustomPage(DataPage.ID, '데이터베이스 (MySQL)', '제조 DB 가 쓸 MySQL 을 고르세요.');
  W := DbPage.SurfaceWidth;
  Indent := ScaleX(20);

  LblDetect := AddLabel(DbPage, '', 0, 0, W, True);
  if OurServiceExists then
    LblDetect.Caption := '이전에 설치한 제조 DB 용 MySQL 서비스(' + SERVICE_NAME + ')가 있습니다. 데이터 폴더가 같으면 그 데이터를 이어서 씁니다.'
  else if (DetectedServices <> '') or Port3306Busy then
  begin
    if DetectedServices <> '' then
      LblDetect.Caption := '이 PC 에 MySQL 이 이미 있습니다 (서비스: ' + DetectedServices + ').'
    else
      LblDetect.Caption := '이 PC 의 포트 3306 을 이미 다른 프로그램(MySQL 로 보임)이 쓰고 있습니다.';
    LblDetect.Caption := LblDetect.Caption + ' 그 MySQL 을 쓰거나, 동봉된 MySQL 을 다른 포트로 따로 설치할 수 있습니다.';
  end
  else
    LblDetect.Caption := '이 PC 에서 MySQL 을 찾지 못했습니다. 동봉된 MySQL 을 설치합니다.';
  Y := LblDetect.Top + LblDetect.Height + ScaleY(10);

  RbBundled := TNewRadioButton.Create(DbPage);
  RbBundled.Parent := DbPage.Surface;
  RbBundled.Top := Y;
  RbBundled.Width := W;
  RbBundled.Caption := '동봉된 MySQL {#MySQLVersion} 설치 (권장)';
  RbBundled.OnClick := @DbChoiceClick;
  Y := Y + RbBundled.Height + ScaleY(6);
  LblBundledPort := AddLabel(DbPage, '포트:', Indent, Y + ScaleY(3), ScaleX(40), False);
  BundledPort := ExpandConstant('{param:DBPORT|}');
  if BundledPort = '' then
  begin
    if Port3306Busy and not OurServiceExists then
      BundledPort := IntToStr(FirstFreePort(3307))
    else
      BundledPort := '3306';
  end;
  EdBundledPort := AddEdit(DbPage, BundledPort, Indent + ScaleX(44), Y, ScaleX(60));
  Y := Y + EdBundledPort.Height + ScaleY(4);
  LblBundledNote := AddLabel(DbPage, 'Windows 서비스 ''' + SERVICE_NAME + ''' 로 등록되어 부팅 때 자동으로 켜집니다. 이 PC 안에서만 접속할 수 있습니다.',
                             Indent, Y, W - Indent, True);
  Y := Y + LblBundledNote.Height + ScaleY(12);

  RbExisting := TNewRadioButton.Create(DbPage);
  RbExisting.Parent := DbPage.Surface;
  RbExisting.Top := Y;
  RbExisting.Width := W;
  RbExisting.Caption := '이 PC 에 이미 있는 MySQL 사용 (8.0.16 이상)';
  RbExisting.OnClick := @DbChoiceClick;
  Y := Y + RbExisting.Height + ScaleY(6);
  LblExistingPort := AddLabel(DbPage, '포트:', Indent, Y + ScaleY(3), ScaleX(40), False);
  EdExistingPort := AddEdit(DbPage, ExpandConstant('{param:DBPORT|3306}'), Indent + ScaleX(44), Y, ScaleX(60));
  Y := Y + EdExistingPort.Height + ScaleY(4);
  LblExistingNote := AddLabel(DbPage, 'root 계정으로 접속해 데이터베이스 ''' + ExpandConstant('{param:DBNAME|orcus}') +
                              ''' 를 만듭니다. 이미 있으면 지우지 않고 그대로 이어서 씁니다.',
                              Indent, Y, W - Indent, True);
  Y := Y + LblExistingNote.Height + ScaleY(14);

  LblPassword := AddLabel(DbPage, '이 PC 에 있는 MySQL 의 root 비밀번호:', 0, Y, W, False);
  Y := Y + LblPassword.Height + ScaleY(4);
  Pw := ExpandConstant('{param:DBPASSWORD|' + DEFAULT_PASSWORD + '}');
  EdPassword := TPasswordEdit.Create(DbPage);
  EdPassword.Parent := DbPage.Surface;
  EdPassword.Top := Y;
  EdPassword.Width := ScaleX(200);
  EdPassword.Text := Pw;
  Y := Y + EdPassword.Height + ScaleY(6);
  LblPasswordHint := AddLabel(DbPage, '기본값은 프로젝트 기본 비밀번호({#DefaultPassword})입니다. 영문·숫자와 ! # $ ^ & * ( ) - _ = + [ ] { } ; : , . ? ~ 만 쓸 수 있습니다. ' +
                              '설치가 끝나면 설치 폴더의 .env 에 저장됩니다.', 0, Y, W, True);

  Mode := Lowercase(ExpandConstant('{param:DBMODE|}'));
  if Mode = 'existing' then
    RbExisting.Checked := True
  else if Mode = 'bundled' then
    RbBundled.Checked := True
  else if OurServiceExists then
    RbBundled.Checked := True
  else if (DetectedServices <> '') or Port3306Busy then
    RbExisting.Checked := True
  else
    RbBundled.Checked := True;
  UpdateDbControls;
end;

procedure InitializeWizard;
var
  Root: String;
begin
  DetectMySQL;
  DataPage := CreateInputDirPage(wpSelectDir, '데이터 저장 위치',
    '원본 백업 보관소와 데이터베이스 파일을 어디에 둘까요?',
    '이 폴더 아래에 archive_vault(원본 백업 보관소)와 mysql_data(동봉 MySQL 의 데이터베이스 파일)가 만들어집니다.' + #13#10 +
    '프로그램 설치 폴더와 다른 곳이어야 하고, 프로그램을 제거해도 보관소는 지우지 않습니다. 영문 경로만 쓸 수 있습니다.',
    False, '');
  DataPage.Add('데이터 폴더:');
  Root := ExpandConstant('{param:DATAROOT|}');
  if Root = '' then
  begin
    if HasFixedDriveD then
      Root := 'D:\Orcus'
    else
      Root := ExpandConstant('{sd}') + '\Orcus';
  end;
  DataPage.Values[0] := Root;
  CreateDbPage;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if (PageID = DataPage.ID) or (PageID = DbPage.ID) then
  begin
    UpgradeMode := IsUpgradeInstall;
    Result := UpgradeMode;
  end;
end;

function CheckDataPage: Boolean;
var
  Root, App: String;
begin
  Result := False;
  Root := RemoveBackslashUnlessRoot(Trim(DataPage.Values[0]));
  App := ExpandConstant('{app}');
  if not IsPlainAsciiPath(Root) or (Length(Root) < 4) then
    SuppressibleMsgBox('데이터 폴더는 드라이브 아래의 영문 경로여야 합니다 (예: D:\Orcus).' + #13#10 +
                       '한글 · 따옴표 · % 는 쓸 수 없습니다 (MySQL 제약).', mbError, MB_OK, IDOK)
  else if IsInside(Root, App) or IsInside(App, Root) then
    SuppressibleMsgBox('데이터 폴더는 프로그램 설치 폴더(' + App + ')의 안쪽이나 바깥 상위 폴더가 아니어야 합니다.' + #13#10 +
                       '프로그램을 지우거나 다시 설치해도 원본 백업이 남도록 따로 둡니다.', mbError, MB_OK, IDOK)
  else
    Result := True;
end;

function CheckDbPage: Boolean;
var
  Port, Probe: Integer;
  Pw, Bad, Version, Tables, Detail, DbName: String;
begin
  Result := False;
  Pw := EdPassword.Text;
  Bad := BadPasswordChars(Pw);
  DbName := ExpandConstant('{param:DBNAME|orcus}');
  if Pw = '' then
  begin
    SuppressibleMsgBox('MySQL root 비밀번호를 입력하세요.', mbError, MB_OK, IDOK);
    Exit;
  end;
  if Bad <> '' then
  begin
    SuppressibleMsgBox('비밀번호에 쓸 수 없는 문자가 있습니다: ' + Bad + #13#10 +
                       '영문·숫자와 ! # $ ^ & * ( ) - _ = + [ ] { } ; : , . ? ~ 만 쓸 수 있습니다.' + #13#10 +
                       '(@ 와 % 는 앱의 DB 접속 문자열을 깨뜨립니다)', mbError, MB_OK, IDOK);
    Exit;
  end;

  if RbBundled.Checked then
  begin
    Port := PortValue(EdBundledPort.Text);
    if Port = 0 then
    begin
      SuppressibleMsgBox('포트는 1024~65535 사이의 숫자여야 합니다.', mbError, MB_OK, IDOK);
      Exit;
    end;
    { 이전에 설치한 OrcusMySQL 이 그 포트를 쓰고 있는 것은 괜찮다 — 설치 중에 멈췄다가 다시 띄운다 }
    if (not OurServiceExists) and PortInUse(Port) then
    begin
      SuppressibleMsgBox('다른 프로그램이 이미 포트(' + IntToStr(Port) + ')를 쓰고 있습니다. 다른 포트를 고르세요 (예: ' +
                         IntToStr(FirstFreePort(Port + 1)) + ').', mbError, MB_OK, IDOK);
      Exit;
    end;
    Result := True;
    Exit;
  end;

  Port := PortValue(EdExistingPort.Text);
  if Port = 0 then
  begin
    SuppressibleMsgBox('포트는 1024~65535 사이의 숫자여야 합니다.', mbError, MB_OK, IDOK);
    Exit;
  end;
  Probe := ProbeExistingMySQL(Port, Pw, Version, Tables, Detail);
  if Probe = 1 then
    SuppressibleMsgBox('MySQL root 비밀번호가 맞지 않습니다 (포트 ' + IntToStr(Port) + ').', mbError, MB_OK, IDOK)
  else if Probe = 2 then
    SuppressibleMsgBox('포트 ' + IntToStr(Port) + ' 에서 MySQL 에 연결할 수 없습니다. MySQL 서비스가 실행 중인지 확인하거나, ' +
                       '동봉된 MySQL 설치를 고르세요.', mbError, MB_OK, IDOK)
  else if Probe = 3 then
  begin
    { 확인 도구를 못 돌린 경우: 막지 않고 설치 마무리 단계에서 다시 확인한다 }
    Result := SuppressibleMsgBox('설치 전에 MySQL 연결을 확인하지 못했습니다:' + #13#10 + Detail + #13#10#13#10 +
                                 '계속하면 설치 마무리 단계에서 다시 확인합니다. 계속할까요?',
                                 mbConfirmation, MB_YESNO, IDYES) = IDYES;
  end
  else if not MySQLVersionOk(Version) then
    SuppressibleMsgBox('이 PC 의 MySQL(' + Version + ')은 쓸 수 없습니다. MySQL 8.0.16 이상이 필요합니다.' + #13#10 +
                       '동봉된 MySQL 설치를 고르세요.', mbError, MB_OK, IDOK)
  else if (StrToIntDef(Tables, 0) > 0) and (ExistingDbChecked <> IntToStr(Port) + '|' + Tables) then
  begin
    Result := SuppressibleMsgBox('이 MySQL 에 이미 데이터베이스 ''' + DbName + ''' (테이블 ' + Tables + '개)가 있습니다.' + #13#10 +
                                 '지우지 않고 그대로 이어서 씁니다 (빠진 테이블만 만듭니다). 계속할까요?',
                                 mbConfirmation, MB_YESNO, IDYES) = IDYES;
    if Result then
      ExistingDbChecked := IntToStr(Port) + '|' + Tables;
  end
  else
    Result := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = DataPage.ID then
    Result := CheckDataPage
  else if CurPageID = DbPage.ID then
    Result := CheckDbPage
  else if CurPageID = wpSelectDir then
  begin
    if not IsPlainAsciiPath(ExpandConstant('{app}')) then
    begin
      SuppressibleMsgBox('설치 폴더는 영문 경로여야 합니다 (예: D:\ManufacturingDB).' + #13#10 +
                         '한글 · 따옴표 · % 는 쓸 수 없습니다 (MySQL 제약).', mbError, MB_OK, IDOK);
      Result := False;
    end;
  end;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo,
  MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine;
  if UpgradeMode then
    Result := Result + '업그레이드:' + NewLine + Space + '데이터베이스 · 데이터 폴더 · 설정(.env)은 지난 설치 그대로 씁니다.' + NewLine +
              Space + '데이터 폴더: ' + GetDataRoot('') + NewLine
  else
  begin
    Result := Result + '데이터 폴더:' + NewLine + Space + GetDataRoot('') + NewLine + NewLine + '데이터베이스:' + NewLine;
    if RbBundled.Checked then
      Result := Result + Space + '동봉 MySQL {#MySQLVersion} 설치 · 서비스 ' + SERVICE_NAME + ' · 포트 ' + Trim(EdBundledPort.Text) + NewLine
    else
      Result := Result + Space + '이 PC 의 MySQL 사용 · 포트 ' + Trim(EdExistingPort.Text) + NewLine;
    Result := Result + Space + '데이터베이스 이름 ' + ExpandConstant('{param:DBNAME|orcus}') + NewLine;
  end;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + MemoTasksInfo + NewLine;
end;

{ ------------------------------------------------------------------ 설치 }

function PythonExe: String;
begin
  Result := ExpandConstant('{app}\runtime\python.exe');
end;

function SetupScriptParams(const Command: String): String;
begin
  Result := '-E -s -X utf8 ' + Q(ExpandConstant('{app}\installer\setup_target.py')) + ' ' + Command +
            ' --app-dir ' + Q(ExpandConstant('{app}'));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Result := '';
  { 업그레이드: 실행 중인 MySQL 이 mysql\bin 의 파일을 잡고 있으면 새 판으로 바꿀 수 없다 }
#ifdef TestMode
  if FileExists(PythonExe) and FileExists(StateFile) then
    Exec(PythonExe, SetupScriptParams('stop-test-mysql'), ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
#else
  if RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\' + SERVICE_NAME) then
  begin
    Log('업그레이드 전에 ' + SERVICE_NAME + ' 서비스를 멈춥니다.');
    Exec(ExpandConstant('{sys}\net.exe'), 'stop ' + SERVICE_NAME, '', SW_HIDE, ewWaitUntilTerminated, Code);
  end;
#endif
end;

function VCRedistReady: Boolean;
var
  Installed, Major, Minor, Bld: Cardinal;
  Key: String;
  RootKey: Integer;
  I: Integer;
begin
  Result := False;
  Key := 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64';
  for I := 0 to 1 do
  begin
    if I = 0 then RootKey := HKLM64 else RootKey := HKLM32;
    if RegQueryDWordValue(RootKey, Key, 'Installed', Installed) and (Installed = 1) and
       RegQueryDWordValue(RootKey, Key, 'Major', Major) and RegQueryDWordValue(RootKey, Key, 'Minor', Minor) and
       RegQueryDWordValue(RootKey, Key, 'Bld', Bld) then
    begin
      Log(Format('VC++ 런타임 %d.%d.%d 설치됨 (동봉 {#VCRedistMajor}.{#VCRedistMinor}.{#VCRedistBld})', [Major, Minor, Bld]));
      Result := (Major > {#VCRedistMajor}) or ((Major = {#VCRedistMajor}) and ((Minor > {#VCRedistMinor}) or
                ((Minor = {#VCRedistMinor}) and (Bld >= {#VCRedistBld}))));
      Exit;
    end;
  end;
end;

function InstallVCRedist: Boolean;
var
  Code: Integer;
begin
  Result := True;
  if VCRedistReady then
    Exit;
  WizardForm.StatusLabel.Caption := 'Microsoft Visual C++ 런타임을 설치하는 중...';
  WizardForm.FilenameLabel.Caption := '';
  if not Exec(ExpandConstant('{tmp}\vc_redist.x64.exe'), '/install /quiet /norestart', '', SW_HIDE,
              ewWaitUntilTerminated, Code) then
  begin
    Log('vc_redist 실행 실패: ' + SysErrorMessage(Code));
    Result := False;
    Exit;
  end;
  Log('vc_redist 종료 코드 ' + IntToStr(Code));
  { 0 설치됨 · 1638 더 새 판이 있음 · 3010 다시 시작해야 마무리됨 }
  if Code = 3010 then
    VCRedistReboot := True
  else if (Code <> 0) and (Code <> 1638) then
    Result := False;
end;

procedure StepOutput(const S: String; const Error, FirstLine: Boolean);
var
  Key: String;
begin
  if Error then
  begin
    Log('설치 마무리 출력 오류: ' + S);
    Exit;
  end;
  Log('  | ' + S);
  if Copy(S, 1, 7) <> '##STEP ' then
    Exit;
  Key := Trim(Copy(S, 8, Length(S)));
  if Key = 'mysql_prepare' then WizardForm.StatusLabel.Caption := 'MySQL 을 준비하는 중...'
  else if Key = 'mysql_init' then WizardForm.StatusLabel.Caption := 'MySQL 데이터 폴더를 초기화하는 중... (1~2분 걸릴 수 있습니다)'
  else if Key = 'mysql_service' then WizardForm.StatusLabel.Caption := 'MySQL 을 Windows 서비스로 등록하는 중...'
  else if Key = 'mysql_start' then WizardForm.StatusLabel.Caption := 'MySQL 을 시작하는 중...'
  else if Key = 'mysql_password' then WizardForm.StatusLabel.Caption := 'MySQL root 계정을 설정하는 중...'
  else if Key = 'mysql_existing' then WizardForm.StatusLabel.Caption := '이 PC 의 MySQL 에 연결하는 중...'
  else if Key = 'mysql_database' then WizardForm.StatusLabel.Caption := '데이터베이스를 준비하는 중...'
  else if Key = 'env_write' then WizardForm.StatusLabel.Caption := '앱 설정(.env)을 쓰는 중...'
  else if Key = 'schema_tables' then WizardForm.StatusLabel.Caption := '테이블을 만드는 중...'
  else if Key = 'schema_folders' then WizardForm.StatusLabel.Caption := '원본 백업 보관소와 data 폴더를 만드는 중...'
  else if Key = 'schema_tools' then WizardForm.StatusLabel.Caption := '기본 공구 목록을 넣는 중...'
  else if Key = 'verify' then WizardForm.StatusLabel.Caption := '설치 결과를 확인하는 중...';
  WizardForm.FilenameLabel.Caption := '';
end;

function ReadErrorText: String;
var
  Lines: TArrayOfString;
  I: Integer;
begin
  Result := '';
  if LoadStringsFromFile(ExpandConstant('{app}\logs\install_error.txt'), Lines) then
    for I := 0 to GetArrayLength(Lines) - 1 do
      Result := Result + Lines[I] + #13#10;
  Result := Trim(Result);
end;

function RunSetupStep(const Command, Caption: String; var Error: String): Boolean;
var
  Params: String;
  Code: Integer;
begin
  WizardForm.StatusLabel.Caption := Caption;
  WizardForm.FilenameLabel.Caption := '';
  Params := SetupScriptParams(Command);
  if UpgradeMode then
    Params := Params + ' --upgrade'
  else if Command = 'mysql' then
    Params := Params + ' --options ' + Q(ExpandConstant('{tmp}\setup_options.json'));
  Log('설치 마무리: ' + Command);
  try
    Result := ExecAndLogOutput(PythonExe, Params, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code, @StepOutput);
  except
    Log(GetExceptionMessage);
    Result := False;
    Code := -1;
  end;
  if not Result then
    Error := '설치된 파이썬 런타임을 실행하지 못했습니다: ' + SysErrorMessage(Code)
  else if Code <> 0 then
  begin
    Result := False;
    Error := ReadErrorText;
    if Error = '' then
      Error := '설치 마무리 단계(' + Command + ')가 코드 ' + IntToStr(Code) + ' 로 끝났습니다.';
  end;
end;

procedure WriteSetupOptions;
var
  Lines: TArrayOfString;
  Mode, Port: String;
begin
  if RbBundled.Checked then
  begin
    Mode := 'bundled';
    Port := IntToStr(PortValue(EdBundledPort.Text));
  end
  else
  begin
    Mode := 'existing';
    Port := IntToStr(PortValue(EdExistingPort.Text));
  end;
  SetArrayLength(Lines, 1);
  Lines[0] := '{"mode": "' + Mode + '", "port": ' + Port + ', "password": "' + JsonEscape(EdPassword.Text) + '", ' +
              '"db_name": "' + JsonEscape(ExpandConstant('{param:DBNAME|orcus}')) + '", "service": "' + SERVICE_NAME + '", ' +
              '"data_root": "' + JsonEscape(GetDataRoot('')) + '", ' +
#ifdef TestMode
              '"no_service": true}';
#else
              '"no_service": false}';
#endif
  SaveStringsToUTF8File(ExpandConstant('{tmp}\setup_options.json'), Lines, False);
end;

{ 같은 이름의 규칙을 지우고 다시 만든다 (업그레이드로 설치 폴더가 바뀌어도 python.exe 경로가 맞게).
  포트만이 아니라 동봉 python.exe 로 묶어, 그 포트를 다른 프로그램이 잡아도 열리지 않게 한다. }
procedure AddFirewallRule(const RuleName, Port: String);
var
  Code: Integer;
  Netsh: String;
begin
  Netsh := ExpandConstant('{sys}\netsh.exe');
  WizardForm.StatusLabel.Caption := 'Windows 방화벽에 포트(' + Port + ')를 여는 중...';
  Exec(Netsh, 'advfirewall firewall delete rule name=' + Q(RuleName), '', SW_HIDE, ewWaitUntilTerminated, Code);
  if Exec(Netsh, 'advfirewall firewall add rule name=' + Q(RuleName) + ' dir=in action=allow protocol=TCP localport=' + Port +
          ' program=' + Q(PythonExe) + ' enable=yes profile=any', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0) then
    Log('방화벽 규칙을 추가했습니다: ' + RuleName)
  else
    Log('방화벽 규칙을 추가하지 못했습니다 (코드 ' + IntToStr(Code) + '): ' + RuleName);
end;

procedure DeleteFirewallRule(const RuleName: String);
var
  Code: Integer;
begin
  Exec(ExpandConstant('{sys}\netsh.exe'), 'advfirewall firewall delete rule name=' + Q(RuleName), '', SW_HIDE,
       ewWaitUntilTerminated, Code);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Error: String;
  Ok: Boolean;
begin
  if CurStep = ssInstall then
    UpgradeMode := IsUpgradeInstall;
  if CurStep <> ssPostInstall then
    Exit;

  WizardForm.ProgressGauge.Style := npbstMarquee;
  try
    Ok := True;
#ifndef TestMode
    if not InstallVCRedist then
    begin
      Ok := False;
      Error := 'Microsoft Visual C++ 런타임을 설치하지 못했습니다. MySQL 을 실행하려면 필요합니다.';
    end;
#endif
    if Ok then
    begin
      if not UpgradeMode then
        WriteSetupOptions;
      Ok := RunSetupStep('mysql', 'MySQL 을 준비하는 중...', Error);
      DeleteFile(ExpandConstant('{tmp}\setup_options.json'));
      if Ok then
        Ok := RunSetupStep('schema', '테이블과 기본 데이터를 만드는 중...', Error);
      if Ok then
        Ok := RunSetupStep('verify', '설치 결과를 확인하는 중...', Error);
    end;
#ifndef TestMode
    if WizardIsTaskSelected('firewall') then
      AddFirewallRule('{#FirewallRule}', '{#DashboardPort}');
    if WizardIsTaskSelected('firewallapi') then
      AddFirewallRule('{#FirewallRuleApi}', '{#ApiPort}')
    else
      DeleteFirewallRule('{#FirewallRuleApi}');
#endif
  finally
    WizardForm.ProgressGauge.Style := npbstNormal;
  end;

  ConfigFailed := not Ok;
  if ConfigFailed then
  begin
    if VCRedistReboot then
      Error := Error + #13#10#13#10 + 'Visual C++ 런타임 설치를 마치려면 PC 를 다시 시작해야 합니다. 다시 시작한 뒤 설치 프로그램을 다시 실행하세요.';
    Log('설치 마무리 실패: ' + Error);
    SuppressibleMsgBox('프로그램 파일은 설치했지만 데이터베이스 설정을 끝내지 못했습니다.' + #13#10#13#10 + Error + #13#10#13#10 +
                       '원인을 고친 뒤 설치 프로그램을 다시 실행하면 이어서 설정합니다.' + #13#10 +
                       '자세한 기록: ' + ExpandConstant('{app}\logs\install.log'), mbCriticalError, MB_OK, IDOK);
  end;
end;

function ConfigSucceeded: Boolean;
begin
  Result := not ConfigFailed;
end;

function NeedRestart: Boolean;
begin
  Result := VCRedistReboot;
end;

function GetCustomSetupExitCode: Integer;
begin
  if ConfigFailed then
    Result := CONFIG_FAILED_EXIT_CODE
  else
    Result := 0;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = DbPage.ID then
    UpdateDbControls
  else if (CurPageID = wpFinished) and ConfigFailed then
  begin
    WizardForm.FinishedHeadingLabel.Caption := '설치를 다 끝내지 못했습니다';
    WizardForm.FinishedLabel.Caption := '프로그램 파일은 설치했지만 데이터베이스 설정을 끝내지 못했습니다.' + #13#10#13#10 +
      '설치 기록(' + ExpandConstant('{app}\logs\install.log') + ')에서 원인을 확인하고, 고친 뒤 설치 프로그램을 다시 실행하세요.';
    WizardForm.AdjustLabelHeight(WizardForm.FinishedLabel);
  end;
end;

{ ------------------------------------------------------------------ 제거 }

{ 설치 폴더에서 실행 중인 프로그램(컨트롤러 · 파이썬 모듈 · 관리 창)이 있으면 1 }
function AppProcessesRunning: Boolean;
var
  Code: Integer;
begin
  Result := Exec(PowerShellExe, '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' +
                 '$app = ''' + AddBackslash(ExpandConstant('{app}')) + '''; ' +
                 'if (Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($app, [StringComparison]::OrdinalIgnoreCase) -and $_.ProcessName -ne ''mysqld'' }) { exit 1 } else { exit 0 }"',
                 '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 1);
end;

procedure StopAppProcesses;
var
  Code: Integer;
begin
  Exec(PowerShellExe, '-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command "' +
       '$app = ''' + AddBackslash(ExpandConstant('{app}')) + '''; ' +
       'Get-Process | Where-Object { $_.Path -and $_.Path.StartsWith($app, [StringComparison]::OrdinalIgnoreCase) -and $_.ProcessName -ne ''mysqld'' } | Stop-Process -Force"',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  if AppProcessesRunning then
    Result := SuppressibleMsgBox('{#ControllerName} 또는 대시보드가 실행 중입니다. 모두 닫고 제거를 계속할까요?',
                                 mbConfirmation, MB_YESNO, IDYES) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  State, Mode, Service, DataDir: String;
  Code: Integer;
  DeleteData: Boolean;
begin
  if CurUninstallStep <> usUninstall then
    Exit;
  StopAppProcesses;
  State := ExpandConstant('{app}\installer\install_state.ini');
  Mode := GetIniString('mysql', 'mode', '', State);
  Service := GetIniString('mysql', 'service', '', State);
  DataDir := GetIniString('mysql', 'datadir', '', State);
  Log('제거: MySQL ' + Mode + ' / 서비스 ' + Service + ' / 데이터 ' + DataDir);

  if Mode = 'bundled' then
  begin
#ifdef TestMode
    Exec(ExpandConstant('{app}\runtime\python.exe'), '-E -s -X utf8 ' + Q(ExpandConstant('{app}\installer\setup_target.py')) +
         ' stop-test-mysql --app-dir ' + Q(ExpandConstant('{app}')), ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
#else
    if Service <> '' then
    begin
      Exec(ExpandConstant('{sys}\net.exe'), 'stop ' + Service, '', SW_HIDE, ewWaitUntilTerminated, Code);
      Exec(ExpandConstant('{sys}\sc.exe'), 'delete ' + Service, '', SW_HIDE, ewWaitUntilTerminated, Code);
      Log('서비스 삭제: 코드 ' + IntToStr(Code));
    end;
#endif
    if (DataDir <> '') and DirExists(DataDir) then
    begin
      if UninstallSilent then
        DeleteData := ExpandConstant('{param:DELETEDBDATA|0}') = '1'
      else
        DeleteData := MsgBox('데이터베이스 파일(' + DataDir + ')도 지울까요?' + #13#10#13#10 +
                             '[아니요] 를 누르면 남겨 두었다가, 다시 설치할 때 같은 데이터 폴더를 고르면 그대로 이어서 씁니다.' + #13#10 +
                             '원본 백업 보관소(archive_vault)는 어느 쪽이든 지우지 않습니다.',
                             mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
      if DeleteData then
      begin
        if DelTree(DataDir, True, True, True) then
          Log('데이터베이스 파일을 지웠습니다: ' + DataDir)
        else
          Log('데이터베이스 파일을 다 지우지 못했습니다: ' + DataDir);
      end;
    end;
  end;

#ifndef TestMode
  DeleteFirewallRule('{#FirewallRule}');
  DeleteFirewallRule('{#FirewallRuleApi}');
#endif
end;
