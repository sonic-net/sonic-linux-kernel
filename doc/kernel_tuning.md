# Kernel configuration options removed

## This document list the kernel options removed from the stock kernel configuration.

### Number of modules and Size comparison

| | Number of .ko modules	| Modules size |
| ----------- | ----------- | ----------- |
|  Before	| 3448 | 196 MB |
|	After | 1991 | 110 MB |

### Kernel options removed

| CONFIG |Short Description |Comment |
| ----------- | ----------- | ----------- |
|CONFIG_REISERFS_FS | Reiserfs support | Unlikely any file system other than EXT4, SQFS will be used |
|CONFIG_JFS_FS |	JFS filesystem support | |
|CONFIG_XFS_FS |	XFS filesystem support | |
|CONFIG_GFS2_FS |	GFS2 file system support | |
|CONFIG_OCFS2_FS |	OCFS2 file system support | |
|CONFIG_NILFS2_FS |	NILFS2 file system support | |
|CONFIG_F2FS_FS |	F2FS filesystem support | |
|CONFIG_ADFS_FS |	ADFS file system support 	 | |
|CONFIG_AFFS_FS |	Amiga FFS file system support | |
|CONFIG_BEFS_FS |	BeOS file system (BeFS) support (read only) | |
|CONFIG_BFS_FS |	BFS file system support | |
|CONFIG_EFS_FS |	EFS file system support (read only) | |
|CONFIG_QNX4FS_FS |	QNX4 file system support (read only) | |
|CONFIG_QNX6FS_FS |	QNX6 file system support (read only) | |
|CONFIG_MINIX_FS |	Minix file system support | Minix was the original Linux filesystem, but was later replaced with ext2. | |
|CONFIG_VXFS_FS |	FreeVxFS file system support (VERITAS VxFS(TM) compatible) | FreeVxFS is a file system driver that support the VERITAS VxFS(TM)  file system format. | |
| | | |
|CONFIG_OMFS_FS |	SonicBlue Optimized MPEG File System support
|CONFIG_ROMFS_FS |	ROM file system support | Very small read-only file system mainly intended for initial ram disks of installation disks, but it could be used for other read-only media as well
|CONFIG_SYSV_FS |	System V/Xenix/V7/Coherent file system support | SCO, Xenix and Coherent are commercial Unix systems for Intel machines, and Version 7 was used on the DEC PDP-11. Saying Y here would allow you to read from their floppies and hard disk partitions.
|CONFIG_UFS_FS |	UFS file system support (read only) | BSD and derivate versions of Unix (such as SunOS, FreeBSD, NetBSD,  OpenBSD and NeXTstep) use a file system called UFS. Some System V Unixes can create and mount hard disk partitions and diskettes using this file system as well. Saying Y here will allow you to read from these partitions
|CONFIG_EXOFS_FS |	EXOFS is a file system that uses an OSD storage device, as its backing storage| Filesystem used on storage device.
|CONFIG_CEPH_FS |	Ceph distributed file system | Experimental Ceph distributed file system.  Ceph is an extremely scalable file system designed to provide high performance, reliable access to petabytes of storage.
|CONFIG_NCP_FS |	NCP file system support (to mount NetWare volumes)| NCP (NetWare Core Protocol) is a protocol that runs over IPX and is used by Novell NetWare clients to talk to file servers.
|CONFIG_CODA_FS |	Coda file system support (advanced network fs) | Coda is an advanced network file system, similar to NFS in that it enables you to mount file systems of a remote server and access them  with regular Unix commands as if they were sitting on your hard disk.
|CONFIG_AFS_FS |	Andrew File System support (AFS) | Experimental Andrew File System driver. It currently only supports unsecured read-only AFS access
|CONFIG_NET_9P |	Plan 9 Resource Sharing Support (9P2000) | Deprecated Operating System
|CONFIG_9P_FS |	Plan 9 Resource Sharing Support (9P2000) | Deprecated Operating System
|CONFIG_DLM |	A general purpose distributed lock manager for kernel or userspace applications. | Used in clustered file systems
|CONFIG_QFMT_V1 |	Old quota format support | Quota format used by kernels earliers than 2.4.22 [deprecated]
| |
|CONFIG_MTD |	Memory Technology Device (MTD) support | Use to handle non managed NAND or NOR flash. I believe that non-managed flash does not make sense in a PC-like device. Flash will be rather presented as USB or SATA interface ("managed NAND").
|CONFIG_JFFS2_FS |	Journalling Flash File System v2 (JFFS2) support | Same comment as with CONFIG_MTD
|CONFIG_UBIFS_FS |	UBIFS file system support | Same comment as with CONFIG_MTD
| |
|CONFIG_PARPORT |	Parallel port support | Deprecated interface.
| |
|CONFIG_AD525X_DPOT |	Analog Devices Digital Potentiometers | This kind of device is not attached to a switch on the i2c Bus.
|CONFIG_IBM_ASM |	Device driver for IBM RSA service processor | Enables device driver support for in-band access to the IBM RSA (Condor) service processor in eServer xSeries systems. This system is not used to run SONiC
|CONFIG_PHANTOM |	Sensable PHANToM (PCI) | Driver for Sensable PHANToM device. This is an haptic device -> no use on a switch.
|CONFIG_SGI_IOC4 |	SGI IOC4 Base IO support | Enables basic support for the IOC4 chip on certain SGI IO controller cards (IO9, IO10, and PCI-RT).  No use on a switch.
| |
|CONFIG_ENCLOSURE_SERVICES |	Enclosure Services | Support for intelligent enclosures (bays which contain storage devices). Used in bays which contain storage devices.
|CONFIG_HP_ILO |	Channel interface driver for the HP iLO processor | Channel interface driver allows applications to communicate with iLO management processors present on HP ProLiant servers. SONiC does not run on HP ProLiant servers
|CONFIG_APDS9802ALS |	Medfield Avago APDS9802 ALS Sensor module | Support for the ALS APDS9802 ambient light sensor. Device not present on a switch (on the I2c bus).
|CONFIG_ISL29003 |	Intersil ISL29003 ambient light sensor | Support for the Intersil ISL29003 ambient light sensor. Device not present on a switch (on the I2c bus).
|CONFIG_ISL29020 |	Intersil ISL29020 ambient light sensor | Support for the Intersil ISL29020 ambient light sensor. Device not present on a switch (on the I2c bus).
|CONFIG_SENSORS_TSL2550 |	Taos TSL2550 ambient light sensor | Support for the Taos TSL2550 ambient light sensor. Device not present on a switch (on the I2c bus).
|CONFIG_SENSORS_BH1770 |	BH1770GLC / SFH7770 combined ALS - Proximity sensor | driver for BH1770GLC (ROHM) or SFH7770 (Osram) combined ambient light and proximity sensor chip. Device not present on a switch (on the I2c bus).
|CONFIG_SENSORS_APDS990X |	APDS990X combined als and proximity sensors | Driver for Avago APDS990x combined ambient light and proximity sensor chip. Device not present on a switch (on the I2c bus).
|CONFIG_HMC6352 |	Honeywell HMC6352 compass | Support for the Honeywell HMC6352 compass
|CONFIG_DS1682 |	Dallas DS1682 Total Elapsed Time Recorder with Alarm | Support for Dallas Semiconductor DS1682 Total Elapsed Time Recorder. Device not present on a switch (on the I2c bus).
|CONFIG_TI_DAC7512 |	Texas Instruments DAC7512 | Support for the Texas Instruments DAC7512 16-bit digital-to-analog converter. Device not present on a switch (on the I2c bus).
|CONFIG_VMWARE_BALLOON |	VMware Balloon Driver
|CONFIG_C2PORT |	Silicon Labs C2 port support | Support for Silicon Labs C2 port used to program Silicon micro controller chips (and other 8051 compatible). Device not attached on a switch (I2c bus).
|CONFIG_SENSORS_LIS3_I2C |	STMicroeletronics LIS3LV02Dx three-axis digital accelerometer | Support for the LIS3LV02Dx accelerometer connected via I2C. Device not attached to a switch.
|CONFIG_VMWARE_VMCI |	VMware VMCI Driver  | VMware's Virtual Machine Communication Interface.  It enables high-speed communication between host and guest in a virtual environment via the VMCI virtual device. SONiC is not supposed to work withing a VMWare VM environment.
|CONFIG_MACINTOSH_DRIVERS |	Macintosh device drivers | Options for devices used with Macintosh computers. These devices are not relevant to a switch system.
| |
|CONFIG_BLK_DEV_FD |	Normal floppy disk support | Deprecated device.
|CONFIG_BLK_DEV_DRBD |	DRBD Distributed Replicated Block Device support | DRBD is a shared-nothing, synchronously replicated block device. It is designed to serve as a building block for high availability clusters and in this context, is a "drop-in" replacement for shared storage. Since this driver is for HA clusters, this is not relevant to SONiC.
|CONFIG_XEN_BLKDEV_FRONTEND |	Xen virtual block device support | This driver implements the front-end of the Xen virtual block device driver. SONiC is not running within XEN, neither does it allows to run another OS within XEN
|CONFIG_BLK_DEV_RBD |	Rados block device (RBD) | Rados block device, which stripes a block device over objects stored in the Ceph distributed object store. RBD is not relevant on a switch.
| |
|CONFIG_FIREWIRE_NOSY |	Nosy - a FireWire traffic sniffer for PCILynx cards | Nosy is an IEEE 1394 packet sniffer that is used for protocol analysis and in development of IEEE 1394 drivers, applications, or firmwares. Only used for protocol analysis and developmenet of IEEE 1394 drivers.
| |
|CONFIG_WLAN |	Wireless LAN | 802.11 wireless is not used on a SONiC switch.
|CONFIG_WIMAX_I2400M_USB |	Intel Wireless WiMAX Connection 2400 over USB (including 5x50 | WIMAX is not used on a SONiC switch.
|CONFIG_IEEE802154_DRIVERS |	IEEE 802.15.4 drivers  | IEEE 802.15.4 Low-Rate Wireless Personal Area Network device drivers
|CONFIG_FUJITSU_ES |	FUJITSU Extended Socket Network Device driver | Support for Extended Socket network device on Extended Partitioning of FUJITSU PRIMEQUEST 2000 E2 series. Not running SONiC.
|CONFIG_HYPERV_NET |	Microsoft Hyper-V virtual network driver | Enable the Hyper-V virtual network driver.
|CONFIG_ISDN |	ISDN support | ISDN ("Integrated Services Digital Network", called RNIS in France) is a fully digital telephone service that can be used for voice and data connections.
| |
|CONFIG_HAMRADIO |	Amateur Radio support | If you want to connect your Linux box to an amateur radio
|CONFIG_CAN |	CAN bus subsystem support | Controller Area Network (CAN) is a slow (up to 1Mbit/s) serial communications protocol which was developed by Bosch in 1991, mainly for automotive, but now widely used in marine (NMEA2000), industrial, and medical applications.
|CONFIG_IRDA |	IrDA (infrared) subsystem support | The Infrared Data Associations (tm) specifies standards for wireless infrared communication and is supported by most laptops and PDA's.
|CONFIG_BT |	Bluetooth subsystem support
|CONFIG_AF_RXRPC |	RxRPC session sockets | These are used for AFS kernel filesystem and userspace utilities.
|CONFIG_WIMAX |	WiMAX Wireless Broadband support | Wireless broadband connectivity using the WiMAX protocol (IEEE 802.16).
|CONFIG_RFKILL |	RF switch subsystem support | control over RF switches found on many WiFi and Bluetooth cards
|CONFIG_CEPH_LIB |	Ceph core library | cephlib, which provides the common functionality to both the Ceph filesystem and to the rados block device (rbd)
|CONFIG_NFC |	NFC subsystem support | NFC (Near field communication)
|CONFIG_NET_DEVLINK |	Network physical/parent device Netlink interface | Network physical/parent device Netlink interface provides infrastructure to support access to physical chip-wide config and monitoring.
| |
|CONFIG_USB_GADGET |	USB device mode | SONiC switch use USB host mode,  not USB device (peripheral).
| |
|CONFIG_INTEL_TH |	Intel(R) Trace Hub controller | Intel(R) Trace Hub (TH) is a set of hardware blocks (subdevices) that produce, switch and output trace data from multiple hardware and software sources over several types of trace output ports encoded in System Trace Protocol (MIPI STPv2) and is intended to perform full system debugging.
|CONFIG_THUNDERBOLT |	Thunderbolt support for Apple devices  | Cactus Ridge Thunderbolt Controller driver. This driver is required if you want to hotplug Thunderbolt devices on Apple hardware.
|CONFIG_CHROME_PLATFORMS |	Platform support for Chrome hardware | Platform support for various Chromebooks and Chromeboxes. Not connected to SONic switches.
| |
|CONFIG_INPUT_JOYDEV |	Joystick interface | Not used on SONiC swiches.
|CONFIG_INPUT_JOYSTICK |	Joysticks/Gamepads | Not used on SONiC swiches.
|CONFIG_INPUT_TABLET |	Tablets | Not used on SONiC swiches.
|CONFIG_INPUT_TOUCHSCREEN |	Touchscreens | Not used on SONiC swiches.
|CONFIG_INPUT_MISC |	Miscellaneous devices | Miscellaneous input drivers. Not used on SONiC swiches.
|CONFIG_GAMEPORT |	Gameport support | Gameport support is for the standard 15-pin PC gameport. Not used on SONiC swiches.
| |
|CONFIG_MEDIA_SUPPORT |	Multimedia support | Not used on SONiC swiches.
|CONFIG_SOUND |	Sound card support | Not used on SONiC swiches.
|CONFIG_UWB |	I th | UWB is a high-bandwidth, low-power, point-to-point radio technology using a wide spectrum (3.1-10.6GHz). Not used on SONiC swiches.
|CONFIG_USB_WDM |	USB Wireless Device Management support | This driver supports the WMC Device Management functionality of cell phones compliant to the CDC WMC specification. You can use AT commands over this device. Not used on SONiC swiches.
|CONFIG_USB_TMC |	USB Test and Measurement Class support | If you want to connect a USB device that follows the USB.org specification for USB Test and Measurement devices to your computer's USB port.
|CONFIG_USB_MDC800 |	USB Mustek MDC800 Digital Camera support | if you want to connect this type of still camera to your computer's USB port. Device not used on SONiC swiches.
|CONFIG_USB_MICROTEK |	Microtek X6USB scanner support | if you want support for the Microtek X6USB and possibly the Phantom 336CX, Phantom C6 and ScanMaker V6U(S)L. Support for anything but the X6 is experimental
|CONFIG_USB_EMI62 |	EMI 6|2m USB Audio interface support | This driver loads firmware to Emagic EMI 6|2m low latency USB Audio and Midi interface.
|CONFIG_USB_STORAGE_KARMA |	Support for Rio Karma music player | additional code to support the Rio Karma USB interface.
|CONFIG_USB_EMI26 |	EMI 2|6 USB Audio interface support | This driver loads firmware to Emagic EMI 2|6 low latency USB Audio interface.
|CONFIG_USB_ADUTUX |	ADU devices from Ontrak Control Systems | if you want to use an ADU device from Ontrak Control Systems.
|CONFIG_USB_SEVSEG |	USB 7-Segment LED Display | Say Y here if you have a USB 7-Segment Display by Delcom
|CONFIG_USB_RIO500 |	USB Diamond Rio500 support | Say Y here if you want to connect a USB Rio500 mp3 player to your computer's USB port.
|CONFIG_USB_LEGOTOWER |	USB Lego Infrared Tower support | Say Y here if you want to connect a USB Lego Infrared Tower to your computer's USB port.  
|CONFIG_USB_LCD |	USB LCD driver support | Say Y here if you want to connect an USBLCD to your computer's USB port. The USBLCD is a small USB interface board for alphanumeric LCD modules.
|CONFIG_USB_CYPRESS_CY7C63 |	Cypress CY7C63xxx USB driver support | Say Y here if you want to connect a Cypress CY7C63xxx micro controller to your computer's USB port.
|CONFIG_USB_CYTHERM |	Cypress USB thermometer driver support | Say Y here if you want to connect a Cypress USB thermometer device to your computer's USB port. This device is also known as the Cypress USB Starter kit or demo board.
|CONFIG_USB_IDMOUSE |	Siemens ID USB Mouse Fingerprint sensor support | Say Y here if you want to use the fingerprint sensor on the Siemens ID Mouse.
|CONFIG_USB_APPLEDISPLAY |	Apple Cinema Display support | Say Y here if you want to control the backlight of Apple Cinema Displays over USB.
|CONFIG_USB_SISUSBVGA |	USB 2.0 SVGA dongle support (Net2280/SiS315) | Say Y here if you intend to attach a USB2VGA dongle based on a Net2280 and a SiS315 chip.
|CONFIG_USB_TRANCEVIBRATOR |	PlayStation 2 Trance Vibrator driver support  | Say Y here if you want to connect a PlayStation 2 Trance Vibrator device to your computer's USB port.
|CONFIG_USB_IOWARRIOR |	IO Warrior driver support | Say Y here if you want to support the IO Warrior devices from Code Mercenaries.
|CONFIG_USB_EHSET_TEST_FIXTURE |	USB EHSET Test Fixture driver  | Say Y here if you want to support the special test fixture device used for the USB-IF Embedded Host High-Speed Electrical Test procedure.
|CONFIG_USB_ISIGHTFW |	iSight firmware loading support | This driver loads firmware for USB Apple iSight cameras
|CONFIG_USB_YUREX |	USB YUREX driver support | Say Y here if you want to connect a YUREX to your computer's USB port. The YUREX is a leg-shakes sensor.
|CONFIG_USB_ATM |	USB DSL modem support | Say Y here if you want to connect a USB Digital Subscriber Line (DSL) modem to your computer's USB port
