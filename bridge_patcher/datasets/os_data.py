"""
os_data.py: OS Version Data
"""

import enum


class os_data(enum.IntEnum):
    # OS Versions, Based off Major Kernel Version
    cheetah =       4 # Actually 1.3.1
    puma =          5
    jaguar =        6
    panther =       7
    tiger =         8
    leopard =       9
    snow_leopard =  10
    lion =          11
    mountain_lion = 12
    mavericks =     13
    yosemite =      14
    el_capitan =    15
    sierra =        16
    high_sierra =   17
    mojave =        18
    catalina =      19
    big_sur =       20
    monterey =      21
    ventura =       22
    sonoma =        23
    sequoia =       24
    tahoe =         25
    golden_gate =   26
    max_os =        99


class os_conversion:

    def os_to_kernel(os: str) -> int:
        """
        Convert OS version to major XNU version

        Parameters:
            os (str): OS version

        Returns:
            int: Major XNU version
        """
        if os.startswith("10."):
            return (int(os.split(".")[1]) + 4)
        else:
            return (int(os.split(".")[0]) + 9)


    def kernel_to_os(kernel: int) -> str:
        """
        Convert major XNU version to OS version

        Parameters:
            kernel (int): Major XNU version

        Returns:
            str: OS version
        """
        if kernel >= os_data.big_sur:
            return str((kernel - 9))
        else:
            return str((f"10.{kernel - 4}"))


    def convert_kernel_to_marketing_name(kernel: int) -> str:
        """
        Convert major XNU version to Marketing name

        Parameters:
            kernel (int): Major XNU version

        Returns:
            str: Marketing name of OS
        """
        try:
            # Find os_data enum name
            os_name = os_data(kernel).name

            # Remove "_" from the string
            os_name = os_name.replace("_", " ")

            # Upper case the first letter of each word
            os_name = os_name.title()
        except ValueError:
            # Handle cases where no enum value exists
            # Pass kernel_to_os() as a substitute for a proper OS name
            os_name = os_conversion.kernel_to_os(kernel)

        return os_name


