-- The void tech hostile with a two-handed Smithscript Dagger, whose jump attacks throw a dagger
-- (TAE type 2 bullet events in both the air and the landed clip, scripts/er-mechanics-voidtech.py):
--   NpcParam 100000030        a generic human (HP 1037, no name)
--   NpcThinkParam 100000030   logic 10000, battle goal 29999
--   CharaInitParam 7154       right hand replaced by Smithscript Dagger +25 (63500000), left unarmed
-- Same brain as the lance (brain_voidtech.lua "voidlance": two hands, then jumps; the R1 is
-- injected frame-exact by scripts/frida/void-trace.js). Spawn it with
--   respawn {"npcParam": 100000030, "think": 100000030, "charaInit": 7154, "path": "dynamic", "count": 1}

VD_THINK = 100000030

lab_brain(VD_THINK, "voidlance")
lab_equip(VD_THINK, "right1", 63500025)
lab_equip(VD_THINK, "left1", 110000)
