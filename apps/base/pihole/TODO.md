# Step 1 Deployment
- Make a pihole deployment with 2 or more replicas.
- Use configmaps for all the settings and blocklists so that the app is stateless.

# Step 2 LoadBalancer
- Make a service of type LoadBalancer for DNS.
- Expose port 53 TCP and 53 UDP.
- The load balancer IP is what you'll use for your DNS.

# Step 3 Admin
3. Make a second service of type ClusterIP for the admin portal. Expose port 80
Ingress for the admin portal service