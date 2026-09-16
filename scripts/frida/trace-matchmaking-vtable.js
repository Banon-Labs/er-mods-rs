// Every slot of `SteamMatchMaking009`, so Seamless's own search names itself.
//
// Two slots have been watched until now -- `RequestLobbyList` and
// `AddRequestLobbyListStringFilter` -- and both read zero across every run on record, with the
// detours byte-verified as installed (`e9` at `lsteamclient+0x8ba60`). That says those two are not
// how Seamless finds a host; it does not say what is. Watching two slots out of forty and calling
// the silence a finding is the same mistake as reading a hook's silence without a control.
//
// So hook the whole vtable and let the call name itself. Nothing inside `ersc.dll` is touched:
// `er_invasion_warp.dll` byte-checks the prologue of every ersc action before calling it, and an
// `Interceptor` there breaks exactly those bytes -- that cost a press earlier today.
//
// Slot names are the public SDK's declaration order for `ISteamMatchmaking` at version 009.
const NAMES = [
  'GetFavoriteGameCount', 'GetFavoriteGame', 'AddFavoriteGame', 'RemoveFavoriteGame',
  'RequestLobbyList', 'AddRequestLobbyListStringFilter', 'AddRequestLobbyListNumericalFilter',
  'AddRequestLobbyListNearValueFilter', 'AddRequestLobbyListFilterSlotsAvailable',
  'AddRequestLobbyListDistanceFilter', 'AddRequestLobbyListResultCountFilter',
  'AddRequestLobbyListCompatibleMembersFilter', 'GetLobbyByIndex', 'CreateLobby', 'JoinLobby',
  'LeaveLobby', 'InviteUserToLobby', 'GetNumLobbyMembers', 'GetLobbyMemberByIndex',
  'GetLobbyData', 'SetLobbyData', 'GetLobbyDataCount', 'GetLobbyDataByIndex', 'DeleteLobbyData',
  'GetLobbyMemberData', 'SetLobbyMemberData', 'SendLobbyChatMsg', 'GetLobbyChatEntry',
  'RequestLobbyData', 'SetLobbyGameServer', 'GetLobbyGameServer', 'SetLobbyMemberLimit',
  'GetLobbyMemberLimit', 'SetLobbyType', 'SetLobbyJoinable', 'GetLobbyOwner', 'SetLobbyOwner',
  'SetLinkedLobby',
];

const api = Process.findModuleByName('steam_api64.dll');
if (api === null) {
  console.log('mm: steam_api64.dll is not loaded');
} else {
  const getUser = api.findExportByName('SteamAPI_GetHSteamUser');
  const findIface = api.findExportByName('SteamInternal_FindOrCreateUserInterface');
  if (getUser === null || findIface === null) {
    console.log('mm: steam_api64.dll does not export the resolver pair');
  } else {
    const user = new NativeFunction(getUser, 'int', [])();
    const version = Memory.allocUtf8String('SteamMatchMaking009');
    const iface = new NativeFunction(findIface, 'pointer', ['int', 'pointer'])(user, version);
    console.log(`mm: hSteamUser=${user} SteamMatchMaking009=${iface}`);
    if (!iface.isNull()) {
      const vtable = iface.readPointer();
      console.log(`mm: vtable=${vtable}`);
      const counts = {};
      for (let i = 0; i < NAMES.length; i++) {
        let fn;
        try {
          fn = vtable.add(i * Process.pointerSize).readPointer();
        } catch (e) {
          continue;
        }
        if (fn.isNull()) {
          continue;
        }
        const name = NAMES[i];
        counts[name] = 0;
        try {
          Interceptor.attach(fn, {
            onEnter() {
              counts[name] += 1;
              if (counts[name] <= 3) {
                console.log(`mm: ${name} (slot ${i}) #${counts[name]}`);
              }
            },
          });
        } catch (e) {
          console.log(`mm: could not hook ${name}: ${e.message}`);
        }
      }
      console.log(`mm: ${Object.keys(counts).length} slot(s) armed -- use the item now`);
      // Self-test: call one harmless slot through the vtable and see the counter move.
      //
      // Three separate zero-readings today were treated as findings before anything in the same
      // attach was shown to fire, and when a control was finally added it read zero too -- so none
      // of them meant anything. `GetLobbyMemberLimit` takes a lobby id and returns an int; passing
      // an invalid id makes it return 0 without touching the network, which is exactly what a
      // control wants.
      try {
        const probe = vtable.add(32 * Process.pointerSize).readPointer();
        new NativeFunction(probe, 'int', ['pointer', 'uint64'])(iface, uint64(0));
        console.log(
          `mm: SELF-TEST GetLobbyMemberLimit counter now ${counts.GetLobbyMemberLimit} ` +
            `(1 or more means hooks in this attach fire; 0 means every zero below is meaningless)`
        );
      } catch (e) {
        console.log(`mm: self-test could not run: ${e.message}`);
      }
    }
  }
}
