"""
Interactive test for pairing flow.
Run this on two devices to test pairing.
"""
from mesh.identity import get_or_create_identity, DeviceType
from mesh.trust_graph import TrustGraph
from mesh.pairing import PairingManager


def main():
    print("\n" + "=" * 60)
    print("AIDE MESH - PAIRING TEST")
    print("=" * 60)
    
    # Get device name
    device_name = input("\nEnter device name (e.g., 'Frank\\'s Mac'): ").strip()
    if not device_name:
        device_name = "Test Device"
    
    # Choose device type
    print("\nDevice types:")
    print("  1. Full Node (runs Ollama)")
    print("  2. Sovereign Terminal (approval only)")
    
    device_type_choice = input("Choose (1 or 2): ").strip()
    device_type = DeviceType.FULL_NODE if device_type_choice == "1" else DeviceType.SOVEREIGN_TERMINAL
    
    # Load or create identity
    identity = get_or_create_identity(device_name, device_type)
    
    # Initialize trust graph
    trust_graph = TrustGraph()
    
    # Create pairing manager
    pairing_mgr = PairingManager(identity, trust_graph)
    
    # Main menu
    while True:
        print("\n" + "=" * 60)
        print("MENU:")
        print("  1. Generate QR code (for pairing)")
        print("  2. Scan peer's QR code (paste QR data)")
        print("  3. List paired devices")
        print("  4. Exit")
        
        choice = input("\nChoose: ").strip()
        
        if choice == "1":
            qr_img = pairing_mgr.initiate_pairing()
            qr_img.show()  # Opens in image viewer
            input("\nPress Enter when peer has scanned...")
        
        elif choice == "2":
            print("\n📸 Paste the QR data from other device:")
            print("(In real app, this would be camera scan)")
            qr_data = input("> ").strip()
            
            if qr_data:
                pairing_mgr.complete_pairing(qr_data)
        
        elif choice == "3":
            pairing_mgr.list_paired_devices()
        
        elif choice == "4":
            print("\nGoodbye!")
            break
        
        else:
            print("\nInvalid choice")


if __name__ == "__main__":
    main()