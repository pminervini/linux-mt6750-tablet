#!/bin/bash
# Laptop side of the tablet USB link: firewall + NAT so the tablet (192.168.77.2) reaches the internet.
set -e
sudo sysctl -qw net.ipv4.ip_forward=1
sudo ufw status | grep -q "192.168.77.0/24" || {
  sudo ufw allow from 192.168.77.0/24 comment 'mt6750 tablet usb link'
  sudo ufw route allow from 192.168.77.0/24 comment 'mt6750 tablet usb link out'
  sudo ufw route allow to 192.168.77.0/24 comment 'mt6750 tablet usb link return'; }
sudo nft list table ip mt6750nat >/dev/null 2>&1 || {
  sudo nft add table ip mt6750nat
  sudo nft 'add chain ip mt6750nat post { type nat hook postrouting priority 100 ; }'
  sudo nft add rule ip mt6750nat post ip saddr 192.168.77.0/24 oifname != "enx*" masquerade; }
echo "host side ready"
