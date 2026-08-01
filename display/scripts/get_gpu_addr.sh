#!/bin/bash
discrete_gpu=""
internal_gpu=""
for gpu in /sys/class/drm/card[0-9]; do
    # if no directory found continue
    [ -e "$gpu" ] || continue

    # get pci-id of card
    pci_addr=$(basename $(readlink -f "$gpu/device"))
    vendor_id=$(cat "$gpu/device/vendor")
    device_id=$(cat "$gpu/device/device")
    
    type="Unknown"

    # Logic for Intel (Vendor 0x8086)
    if [[ "$vendor_id" == "0x8086" ]]; then
        # Check for typical integrated adrress
        if [[ "$pci_addr" == "0000:00:02.0" ]]; then
            type="Integrated"
            internal_gpu=$pci_addr
        else
            type="Discrete"
            discrete_gpu=$pci_addr
        fi

    # Logic for AMD (Vendor 0x1002)
    elif [[ "$vendor_id" == "0x1002" ]]; then
        # Search on Northbridge in hwmon folder
        if ls "$gpu/device/hwmon"/hwmon*/in1_input >/dev/null 2>&1; then
            type="Integrated"
            internal_gpu=$pci_addr
        else
            type="Discrete"
            discrete_gpu=$pci_addr
        fi

    # Logic for NVIDIA (Vendor 0x10de)
    elif [[ "$vendor_id" == "0x10de" ]]; then
        # NVIDIA ist almost always descrete (except for old Tegra-Chips)
        type="Discrete"
        discrete_gpu=$pci_addr
    fi
done

echo $discrete_gpu-$internal_gpu
        