-- The void tech hostile, built from game rows nothing else in the lab touches:
--   NpcParam 100000010        a generic human (HP 1037, no name)
--   NpcThinkParam 100000010   logic 10000, battle goal 29999, the same scripts every human NPC runs
--   CharaInitParam 7154       a Finger Seal +25 in each hand and Bestial Sling (MagicParam 6800) in
--                             spell slot 1, Faith 80
-- Its only brain is brain_voidtech.lua, its only spell Bestial Sling (the row's other spells are
-- overwritten), and it fights the player as an enemy. Spawn it with
--   respawn {"npcParam": 100000010, "think": 100000010, "charaInit": 7154, "path": "dynamic", "count": 1}
-- 'dynamic' (WorldChrManImp::SpawnDynamicChr) builds it from those rows; the default 'summon' path
-- copies the summoner. Delete this file and save to send the think row back to the stock AI.

VT_THINK = 100000010

lab_brain(VT_THINK, "voidtech")
lab_team(6)
lab_spells(VT_THINK, "6800")
