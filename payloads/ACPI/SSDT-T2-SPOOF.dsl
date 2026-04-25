DefinitionBlock ("", "SSDT", 2, "T2FIX", "T2SPOOF", 0x00001000)
{
    External (_SB.PCI0.RP01, DeviceObj)
    External (_SB.PCI0.RP01.XDSM, MethodObj)

    Scope (_SB.PCI0.RP01)
    {
        Method (_DSM, 4, NotSerialized)
        {
            If (LEqual (Arg0, ToUUID ("a0b5b7c6-2d8a-4c2f-81d1-05d54930d0a5")))
            {
                Return (Package ()
                {
                    "apple-coprocessor-version", 
                    Buffer () { "23.16.14000.0.0,0" }
                })
            }

            Return (XDSM (Arg0, Arg1, Arg2, Arg3))
        }
    }
}