# NPC stealth detection: how the engine decides "detected" (1.6.1170)

Durable copy of the 2026-11-11 read. The project-side card with the design context is MFO
`Docs/research/2026-11/calculate-detection-re.md` (branch `docs/g2-hide`). Grades: DISASM = read on the unpacked
1.6.1170 binary (proves the path exists, not that it runs), LIKELY = inferred meaning, UNREAD, FIELD = seen in a log.
Nothing here is FIELD yet. 1.5.97 and 1.7.104 were not re-read (an earlier pass found CalculateDetection's
prologue identical on all three and the bodies matching by instruction count). GMST values are image defaults: a
plugin can override them, read them at runtime by name.

## Call chain (AE ids, RVAs)

- Detection manager job, id 42742 0x7B0710, the ONLY caller of CalculateDetection (call at 0x7B0D8B). Computes a
  pair only if the tdetect byte `[*(0x20F69B0)+1]` is set, neither form has flags 0x820, the observer is within 8192
  units of the player and the target within 8192 of the observer. No player-only test. DISASM.
- CalculateDetection, id 37774 0x690870. Signature (DISASM):
  `(Actor* observer, Actor* target, int* level /*caller inits -1000*/, bool* inViewCone, bool* los, int* losResult,
  NiPoint3* where, float* soundOut, float* lightOut, float* skillOut)`. Returns without writing if either actor has
  no currentProcess (AE +0xF8). Re-verify against the binary before hooking (CommonLib declares none of this).
- Three roads, first positive wins: sight formula (id 26365, inside range id 26364), noise event (id 26366, the
  target's newest noise record, within fDetectEventDistanceNPC 512 for NPC targets vs Player 1536, only when either
  actor has a combatController AE +0x160), projectile (fDetectProjectileDistanceNPC 512 vs Player 1024). A sight
  score of exactly 0 becomes level -1.
- Results go to the store (id 42760) and the listeners (id 42704); `CombatGroup::UpdateTargetDetection`
  (0x805090) is the combat group's listener.
- Cached light level of an actor: id 39509 reads `AIProcess->middleHigh + 0x3A8` (AE). Written only by id 39510
  from id 39946, which recalculates every fLightRecalcTimerPlayer (0.5 s) for the player, fLightRecalcTimer (2 s)
  for NPCs, and only when middleHigh byte +0x328 is set or the actor is in an interior cell (the byte is unidentified).

## The formula, id 26365 (DISASM)

O = observer, T = target.
```
alert      = 1 + O.inCombat*fSneakAlertMod(0.2) + O.asleep*fSneakSleepMod(-0.2)
               + O.fightingSomeoneElse*fSneakCombatMod(-0.4) + ambush(+2.5 target / -0.25 non-target)
perception = 10 + (90-10) * O.SneakAV/100                     (fSneakPerceptionSkillMin/Max)
maxDist    = 1500 * (exterior?2) * (flying?2)                 (fSneakMaxDistance, ...ExteriorDistanceMult, ...FlyingDistanceMult)
distF      = max(0,(maxDist-dist)/maxDist)^2                  (fSneakDistanceAttenuationExponent)
sound      = max(0,(fSneakActionMult*T.actionValue + (running?2:1)*moving*max(T.MovementNoiseMult,0)*(12+0.5*T.equippedWeight))
                   * 1.6 * distF * (noLOS && ambush!=1 ? 0.25 : 1) * (O.asleep ? 0.5 : 1))
lightMove  = 1 + 0.01*moving + 0.2*running, then perk entry 48 ModDetectionMovement (owner T)
light      = T.cachedLight (*3 if O has NightEye), minus iLightLevelExteriorMod(0) or min(ambient*100, iLightLevelInteriorMod 50),
             clamp 0..iLightLevelMax(100), then perk entry 47 ModDetectionLight (owner T)
lightTerm  = max(0,(inViewCone && T.Invisibility<=0) * distF * lightMove * light*(exterior?1:1.4)
                   * (O.asleep?0:(100-O.Blindness)/100) * raceSize(0.5/1/1.5/2))
skillTerm  = (distF*alert*perception - (T.IsSneaking ? T.SneakAV after perk entry 57 ModDetectionSneakSkill : 0)) * 0.5
score      = -25 + sound + lightTerm + skillTerm               (fSneakBaseValue)
level      = score>=1 ? int(score) : score>0 ? 1 : int(score)
```
Perk entry 15 IgnoreRunningDuringDetection (owner T) clears `running`, and `moving` too while T sneaks. The only
player branches: `skill*fPlayerDetectionSneakMult+fPlayerDetectionSneakBase` (1, 0) for the player, the noise and
projectile reach above, and fDetectionCombatNonTargetDistanceMult 0.75 for NON-player targets that are not O's combat
target. So an NPC is scored like the player and is never easier to detect.

Range (id 26364 + CalculateDetection): `1500*(ext?2)*(fly?2)`, times 0.75 per the rule above, shrunk past
fDetectionLOSDistanceAngle (60 deg) toward fDetectionLOSDistanceMultExterior 0.5 / Interior 0.75 at 180 deg. View
cone = LOS and angle < fDetectionViewCone(190)/2.

## What the combat group does with it (0x805090, DISASM, flag bytes LIKELY)

CombatTarget (fork `CombatGroup.h`): +0x04 detectLevel, +0x08 stealthPoints, +0x90 lastKnownTimeStamp, +0xA6 flags
(bit 0 known, bit 1 lost).
- The group keeps the highest level any member reported.
- While stealthPoints > 0 a positive level notices the target only above iCombatStealthPointSneakDetectionThreshold
  (70) for a sneaking target or iCombatStealthPointDetectionThreshold (25) otherwise.
- Lost after fCombatDetectionLostTimeLimit (10 s) without a positive level.
- Lost AT ONCE on a negative level when the result's byte +0x14 (LIKELY the view-cone out) is set, unless the target
  was known this pass and is visible. An invisible target (AV 54 > 0) is lost at once even when known.
- Lost on a negative level near the last known point (fCombatDetectionLostCheckNoticedDistance 256, check id 47311
  UNREAD).

Practical rule: the engine already hides an NPC that scores <= 0: at once from a foe looking its way, within 10 s
from one that is not. A forced non-detection seat is only needed to remove that timer or if the job does not
compute a pair.
