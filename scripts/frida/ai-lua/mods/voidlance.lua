-- The void tech hostile with a two-handed Sword Lance, on game rows nothing else in the lab touches:
--   NpcParam 100000020        a generic human (HP 1037, no name)
--   NpcThinkParam 100000020   logic 10000, battle goal 29999
--   CharaInitParam 7154       the caster's row; its right hand is replaced by Sword Lance +25
--                             (EquipParamWeapon 3500000, great spear) and its left by unarmed
-- Its brain is brain_voidtech.lua in "voidlance" mode: two hands on the lance, then jump R1s at
-- its target with the press delay swept, standing still. Spawn it with
--   respawn {"npcParam": 100000020, "think": 100000020, "charaInit": 7154, "path": "dynamic", "count": 1}

VL_THINK = 100000020

lab_brain(VL_THINK, "voidlance")
lab_equip(VL_THINK, "right1", 3500025)
lab_equip(VL_THINK, "left1", 110000)
